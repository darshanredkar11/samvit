"""
say  — send a message to an agent or broadcast
read — fetch unread messages for the calling agent

Decisions applied:
  #4  say to unknown handle → 404 (LookupError)
  #9  body size capped at 64 KB
  #11 message_reads join table (no read_by UUID[] array)
"""

from __future__ import annotations

import logging

from samvit import db, guard

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024   # Decision #9: 64 KB
DEFAULT_READ_LIMIT = 20
MAX_READ_LIMIT = 200


# ── say ───────────────────────────────────────────────────────────────────────

async def say(
    agent: dict,
    body: str,
    to: str | None = None,
    topic: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """
    Send a directed message (to=handle) or broadcast (to=None).
    Decision #4: unknown handle → LookupError (mapped to 404 in main.py).
    Decision #9: body > 64 KB → ValueError (mapped to 400).
    """
    if not body or not body.strip():
        raise ValueError("body must not be empty")

    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise ValueError(
            f"body exceeds maximum size of {MAX_BODY_BYTES // 1024} KB"
        )

    if metadata is None:
        metadata = {}

    # ── Ethical guard: scan message body before storing ──────────────────────
    body = await guard.apply(body.strip(), agent["id"], "input", "say")

    to_agent_id: str | None = None

    async with db.pool().acquire() as conn:
        # Decision #4: validate recipient handle
        if to:
            row = await conn.fetchrow(
                "SELECT id FROM agents WHERE handle = $1 AND workspace_id = $2",
                to.lower().strip(), agent["workspace_id"],
            )
            if not row:
                raise LookupError(f"Agent '{to}' not found")
            to_agent_id = str(row["id"])

        message_id = await conn.fetchval(
            """
            INSERT INTO messages (from_agent, to_agent, topic, body, metadata, workspace_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            agent["id"],
            to_agent_id,
            topic,
            body.strip(),
            metadata,
            agent["workspace_id"],
        )

    log.debug("say: from=%s to=%s topic=%s id=%s", agent["handle"], to, topic, message_id)
    return {"message_id": str(message_id)}


# ── read ──────────────────────────────────────────────────────────────────────

async def read(
    agent: dict,
    topic: str | None = None,
    from_handle: str | None = None,
    limit: int = DEFAULT_READ_LIMIT,
    mark_read: bool = True,
) -> dict:
    """
    Return unread messages for the calling agent (directed + broadcast).
    Decision #11: uses message_reads join table, not a UUID[] array.
    mark_read=False: peek without consuming (Decision §6.6).
    """
    if limit <= 0:
        raise ValueError("limit must be > 0")
    limit = min(limit, MAX_READ_LIMIT)

    async with db.pool().acquire() as conn:
        # Build the query dynamically based on optional filters
        params: list = [agent["id"], agent["workspace_id"], limit]
        filters: list[str] = [
            # Scoped to agent's workspace
            "m.workspace_id = $2",
            # Directed to me OR broadcast — but NOT already read by me
            """
            (m.to_agent = $1 OR m.to_agent IS NULL)
            AND NOT EXISTS (
                SELECT 1 FROM message_reads mr
                 WHERE mr.message_id = m.id
                   AND mr.agent_id = $1
            )
            """
        ]

        if topic:
            params.append(topic)
            filters.append(f"m.topic = ${len(params)}")

        if from_handle:
            params.append(from_handle.lower().strip())
            filters.append(
                f"EXISTS (SELECT 1 FROM agents a WHERE a.id = m.from_agent AND a.handle = ${len(params)})"
            )

        where = " AND ".join(filters)

        if mark_read:
            rows = await conn.fetch(
                f"""
                WITH candidates AS (
                    SELECT m.id
                      FROM messages m
                     WHERE {where}
                     ORDER BY m.created_at ASC
                     LIMIT $3
                ),
                marked AS (
                    INSERT INTO message_reads (message_id, agent_id)
                    SELECT id, $1 FROM candidates
                    ON CONFLICT DO NOTHING
                    RETURNING message_id
                )
                SELECT m.id, m.body, m.topic, m.metadata, m.created_at,
                       a.handle AS from_handle
                  FROM marked
                  JOIN messages m ON m.id = marked.message_id
                  JOIN agents a ON a.id = m.from_agent
                 ORDER BY m.created_at ASC
                """,
                *params,
            )
        else:
            rows = await conn.fetch(
                f"""
                SELECT m.id, m.body, m.topic, m.metadata, m.created_at,
                       a.handle AS from_handle
                  FROM messages m
                  JOIN agents a ON a.id = m.from_agent
                 WHERE {where}
                 ORDER BY m.created_at ASC
                 LIMIT $3
                """,
                *params,
            )

    # ── Ethical guard: scan output before delivering to LLM ─────────────────
    cleaned = []
    for r in rows:
        clean_body = await guard.apply(r["body"], agent["id"], "output", "read",
                                       check_entropy=False)
        cleaned.append((r, clean_body))

    return {
        "messages": [
            {
                "id":       str(r["id"]),
                "from":     r["from_handle"],
                "body":     clean_body,
                "topic":    r["topic"],
                "metadata": dict(r["metadata"]),
                "sent_at":  r["created_at"].isoformat(),
            }
            for r, clean_body in cleaned
        ]
    }
