"""
remember — store a memory (vector + optional KV)
recall  — retrieve memories by semantic search or exact key

Decisions applied:
  #3  Namespace rules: own handle = private; 'global' = shared; writing to
      another agent's private namespace is rejected with 403
  #5  KV miss returns empty results, not 404
  #6  KV + vector writes are wrapped in a single transaction
  #7  IVFFlat fallback: pgvector uses seq scan automatically when row count
      is low; we add SET enable_indexscan=off hint for tables < 100 rows
  #9  Content size validated in embeddings.embed() before any DB call
"""

from __future__ import annotations

import logging
from typing import Any

from samvit import db, embeddings, guard
from samvit.guard import GuardError

log = logging.getLogger(__name__)

MAX_RECALL_LIMIT = 50
DEFAULT_RECALL_LIMIT = 5
DEFAULT_MIN_SCORE = 0.0


# ── remember ──────────────────────────────────────────────────────────────────

async def remember(
    agent: dict,
    content: str,
    key: str | None = None,
    namespace: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """
    Store content as a vector embedding. If key is supplied, also upsert KV.
    Both writes happen in one transaction (Decision #6).
    """
    ns = _resolve_namespace(namespace, agent["handle"])
    _assert_write_allowed(ns, agent["handle"])

    if metadata is None:
        metadata = {}

    # ── Ethical guard: scan input before embedding or storing ────────────────
    content = await guard.apply(content, agent["id"], "input", "remember")

    # Embed first — if this fails we haven't touched the DB
    vector = await embeddings.embed(content)

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            # Vector write
            memory_id = await conn.fetchval(
                """
                INSERT INTO semantic_memory
                    (agent_id, namespace, content, embedding, metadata)
                VALUES ($1, $2, $3, $4::vector, $5)
                RETURNING id
                """,
                agent["id"],
                ns,
                content,
                vector,
                metadata,
            )

            # KV upsert (Decision #6: same transaction)
            if key:
                if not key.strip():
                    raise ValueError("key must not be empty")
                await conn.execute(
                    """
                    INSERT INTO kv_memory (agent_id, namespace, key, value, updated_at)
                    VALUES ($1, $2, $3, $4, now())
                    ON CONFLICT (namespace, key)
                    DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                    """,
                    agent["id"],
                    ns,
                    key.strip(),
                    {"text": content, **metadata},
                )

    log.debug("remember: agent=%s ns=%s key=%s id=%s", agent["handle"], ns, key, memory_id)
    return {"id": str(memory_id), "stored": True}


# ── recall ────────────────────────────────────────────────────────────────────

async def recall(
    agent: dict,
    query: str | None = None,
    key: str | None = None,
    namespace: str | None = None,
    limit: int = DEFAULT_RECALL_LIMIT,
    min_score: float = DEFAULT_MIN_SCORE,
) -> dict:
    """
    Retrieve memories. Key lookup takes priority over vector search.
    Decision #5: KV miss → empty results, not 404.
    """
    ns = _resolve_namespace(namespace, agent["handle"])

    # Validate limit
    if limit <= 0:
        raise ValueError("limit must be > 0")
    limit = min(limit, MAX_RECALL_LIMIT)

    if min_score < 0.0 or min_score > 1.0:
        raise ValueError("min_score must be between 0.0 and 1.0")

    # ── KV exact lookup ───────────────────────────────────────────────────────
    if key:
        return await _kv_recall(ns, key.strip())

    # ── Vector search ─────────────────────────────────────────────────────────
    if not query or not query.strip():
        raise ValueError("Either query or key is required")

    return await _vector_recall(ns, query, limit, min_score)


async def _kv_recall(namespace: str, key: str) -> dict:
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT kv.value, a.handle, kv.updated_at
              FROM kv_memory kv
              JOIN agents a ON a.id = kv.agent_id
             WHERE kv.namespace = $1
               AND kv.key = $2
            """,
            namespace, key,
        )
    if not row:
        return {"results": []}   # Decision #5: empty array, not 404

    return {
        "results": [
            {
                "key": key,
                "content": row["value"].get("text", ""),
                "value": row["value"],
                "agent": row["handle"],
                "updated_at": row["updated_at"].isoformat(),
            }
        ]
    }


async def _vector_recall(
    namespace: str,
    query: str,
    limit: int,
    min_score: float,
) -> dict:
    vector = await embeddings.embed(query)

    async with db.pool().acquire() as conn:
        # Decision #7: check row count; pgvector uses seq scan automatically
        # when the IVFFlat index isn't beneficial (< lists rows), but we
        # explicitly disable the index scan for very small tables to avoid
        # the planner choosing a potentially unhelpful index path.
        row_count: int = await conn.fetchval(
            "SELECT COUNT(*) FROM semantic_memory WHERE namespace = $1", namespace
        )

        if row_count < 100:
            await conn.execute("SET LOCAL enable_indexscan = off")

        rows = await conn.fetch(
            """
            SELECT
                sm.id,
                sm.content,
                sm.metadata,
                sm.created_at,
                a.handle,
                1 - (sm.embedding <=> $1::vector) AS score
              FROM semantic_memory sm
              JOIN agents a ON a.id = sm.agent_id
             WHERE sm.namespace = $2
               AND 1 - (sm.embedding <=> $1::vector) >= $3
             ORDER BY sm.embedding <=> $1::vector
             LIMIT $4
            """,
            vector,
            namespace,
            min_score,
            limit,
        )

    # ── Ethical guard: scan output before returning to LLM ──────────────────
    cleaned_rows = []
    for r in rows:
        clean_content = await guard.apply(r["content"], agent["id"], "output", "recall",
                                          check_entropy=False)
        cleaned_rows.append((r, clean_content))

    return {
        "results": [
            {
                "id": str(r["id"]),
                "content": clean_content,
                "score": round(float(r["score"]), 4),
                "agent": r["handle"],
                "namespace": namespace,
                "metadata": dict(r["metadata"]),
                "created_at": r["created_at"].isoformat(),
            }
            for r, clean_content in cleaned_rows
        ]
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_namespace(namespace: str | None, agent_handle: str) -> str:
    """
    Decision #3 + recall §14:
    - None / empty → caller's own private namespace
    - 'global'     → shared namespace
    - anything else → taken as-is (but write_allowed check applies)
    """
    if not namespace or not namespace.strip():
        return agent_handle
    return namespace.strip()


def _assert_write_allowed(namespace: str, agent_handle: str) -> None:
    """
    Decision #3: agents may write to their own namespace or 'global'.
    Writing to another agent's private namespace is rejected.
    """
    if namespace == "global" or namespace == agent_handle:
        return
    raise PermissionError(
        f"Cannot write to namespace '{namespace}' — "
        f"you may only write to your own namespace ('{agent_handle}') or 'global'"
    )
