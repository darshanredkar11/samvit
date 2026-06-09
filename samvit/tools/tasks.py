"""
claim — atomically claim the next available task
done  — complete or fail a claimed task

Decisions applied:
  #8  Grace period: cleanup runs at claimed_at + timeout + 5min (cleanup.py)
  #9  result size capped at 1 MB
  #13 Redpanda publish failure is logged, not raised
  #15 Tags = OR filter using PostgreSQL && operator

PostgreSQL note: UPDATE…JOIN is not valid syntax.
All claim queries use CTEs (WITH … UPDATE … RETURNING) to atomically
select + lock + update in one round-trip.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid

from samvit import db, events

log = logging.getLogger(__name__)

MAX_RESULT_BYTES = 1 * 1024 * 1024   # Decision #9: 1 MB


# ── claim ─────────────────────────────────────────────────────────────────────

async def claim(
    agent: dict,
    tags: list[str] | None = None,
    task_id: str | None = None,
) -> dict:
    """
    Atomically claim the next pending task.
    Uses CTE + FOR UPDATE SKIP LOCKED to prevent double-claims.
    Decision #15: tags is an OR filter (&&).
    Returns {"task": <dict>} or {"task": null}.
    """
    claim_token = secrets.token_urlsafe(32)

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            if task_id:
                row = await _claim_specific(conn, agent, task_id, claim_token)
            else:
                row = await _claim_next(conn, agent, tags or [], claim_token)

    if not row:
        return {"task": None}

    return {
        "task": {
            "id":                    str(row["id"]),
            "title":                 row["title"],
            "description":           row["description"],
            "claim_token":           claim_token,
            "priority":              row["priority"],
            "tags":                  list(row["tags"]),
            "deadline":              row["deadline"].isoformat() if row["deadline"] else None,
            "claim_timeout_minutes": int(row["claim_timeout"].total_seconds() // 60),
            "created_by":            row["created_by_handle"],
        }
    }


async def _claim_next(conn, agent: dict, tags: list[str], claim_token: str):
    """
    CTE pattern:
      1. selected  — find and lock the best pending task (SKIP LOCKED)
      2. updated   — atomically set it to 'claimed'
      3. Final SELECT — join with agents to get creator handle
    """
    if tags:
        # Decision #15: OR filter — task matches if its tags overlap with requested tags
        return await conn.fetchrow(
            """
            WITH selected AS (
                SELECT id FROM tasks
                 WHERE status = 'pending'
                   AND tags && $3::text[]
                 ORDER BY priority DESC, created_at ASC
                 LIMIT 1
                 FOR UPDATE SKIP LOCKED
            ),
            updated AS (
                UPDATE tasks
                   SET status      = 'claimed',
                       claimed_by  = $1,
                       claimed_at  = now(),
                       claim_token = $2
                  FROM selected
                 WHERE tasks.id = selected.id
                RETURNING tasks.id, tasks.title, tasks.description,
                          tasks.priority, tasks.tags, tasks.deadline,
                          tasks.claim_timeout, tasks.created_by
            )
            SELECT u.*, COALESCE(a.handle, 'unknown') AS created_by_handle
              FROM updated u
              LEFT JOIN agents a ON a.id = u.created_by
            """,
            agent["id"], claim_token, tags,
        )
    else:
        return await conn.fetchrow(
            """
            WITH selected AS (
                SELECT id FROM tasks
                 WHERE status = 'pending'
                 ORDER BY priority DESC, created_at ASC
                 LIMIT 1
                 FOR UPDATE SKIP LOCKED
            ),
            updated AS (
                UPDATE tasks
                   SET status      = 'claimed',
                       claimed_by  = $1,
                       claimed_at  = now(),
                       claim_token = $2
                  FROM selected
                 WHERE tasks.id = selected.id
                RETURNING tasks.id, tasks.title, tasks.description,
                          tasks.priority, tasks.tags, tasks.deadline,
                          tasks.claim_timeout, tasks.created_by
            )
            SELECT u.*, COALESCE(a.handle, 'unknown') AS created_by_handle
              FROM updated u
              LEFT JOIN agents a ON a.id = u.created_by
            """,
            agent["id"], claim_token,
        )


async def _claim_specific(conn, agent: dict, task_id: str, claim_token: str):
    """Claim a specific task by ID. Raises if not found or not pending."""
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise ValueError(f"Invalid task_id: {task_id!r}")

    # Check existence and status with a lock
    row = await conn.fetchrow(
        "SELECT id, status FROM tasks WHERE id = $1 FOR UPDATE SKIP LOCKED",
        tid,
    )
    if not row:
        raise LookupError(f"Task {task_id} not found or already locked")
    if row["status"] != "pending":
        raise ValueError(f"Task {task_id} is not pending (status={row['status']})")

    return await conn.fetchrow(
        """
        WITH updated AS (
            UPDATE tasks
               SET status      = 'claimed',
                   claimed_by  = $1,
                   claimed_at  = now(),
                   claim_token = $2
             WHERE id = $3
            RETURNING id, title, description, priority, tags,
                      deadline, claim_timeout, created_by
        )
        SELECT u.*, COALESCE(a.handle, 'unknown') AS created_by_handle
          FROM updated u
          LEFT JOIN agents a ON a.id = u.created_by
        """,
        agent["id"], claim_token, tid,
    )


# ── done ──────────────────────────────────────────────────────────────────────

async def done(
    agent: dict,
    task_id: str,
    claim_token: str,
    result: dict | None = None,
    status: str = "done",
) -> dict:
    """
    Mark a task done or failed. Validates claim_token ownership.
    Decision #9: result capped at 1 MB.
    Decision #13: Redpanda publish failure is swallowed.
    """
    if status not in ("done", "failed"):
        raise ValueError(f"status must be 'done' or 'failed', got {status!r}")

    if result is not None:
        result_bytes = len(json.dumps(result).encode())
        if result_bytes > MAX_RESULT_BYTES:
            raise ValueError(
                f"result exceeds maximum size of {MAX_RESULT_BYTES // (1024 * 1024)} MB"
            )

    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise ValueError(f"Invalid task_id: {task_id!r}")

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, claim_token, claimed_by FROM tasks WHERE id = $1",
            tid,
        )
        if not row:
            raise LookupError(f"Task {task_id} not found")

        if row["status"] != "claimed":
            raise ValueError(
                f"Task {task_id} cannot be completed — current status: {row['status']}"
            )

        if not row["claim_token"] or row["claim_token"] != claim_token:
            raise PermissionError("claim_token does not match — cannot complete this task")

        await conn.execute(
            """
            UPDATE tasks
               SET status      = $1::task_status,
                   done_at     = now(),
                   result      = $2,
                   claim_token = NULL
             WHERE id = $3
            """,
            status, result, tid,
        )

    log.info("Task %s marked %s by agent %s", task_id, status, agent["handle"])

    # Decision #13: publish event; failure is non-fatal
    event_name = "task.completed" if status == "done" else "task.failed"
    await events.publish(event_name, {
        "task_id": task_id,
        "status": status,
        "agent": agent["handle"],
        "result": result,
    })

    return {"ok": True}
