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

from samvit import db, guard

log = logging.getLogger(__name__)

MAX_RESULT_BYTES = 1 * 1024 * 1024   # Decision #9: 1 MB
MAX_TASK_LIMIT = 200


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


async def create(
    agent: dict,
    title: str,
    description: str | None = None,
    tags: list[str] | None = None,
    priority: int = 0,
    worker_type: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Create a task, optionally idempotent within the creating agent."""
    title = title.strip()
    if not title:
        raise ValueError("title must not be empty")
    if len(title) > 500:
        raise ValueError("title must be 500 characters or fewer")

    title = await guard.apply(title, agent["id"], "input", "create_task")
    if description:
        description = await guard.apply(description, agent["id"], "input", "create_task")

    task_tags = list(dict.fromkeys(tags or []))
    if worker_type and worker_type not in task_tags:
        task_tags.append(worker_type)

    async with db.pool().acquire() as conn:
        task_id = await conn.fetchval(
            """
            INSERT INTO tasks
                (title, description, tags, priority, worker_type, created_by,
                 idempotency_key, workspace_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (created_by, idempotency_key)
                WHERE idempotency_key IS NOT NULL
            DO UPDATE SET idempotency_key = EXCLUDED.idempotency_key
            RETURNING id
            """,
            title, description, task_tags, priority, worker_type,
            agent["id"], idempotency_key, agent["workspace_id"],
        )
    return {"task_id": str(task_id), "created": True}


async def list_tasks(
    agent: dict,
    status: str | None = None,
    tags: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List tasks visible to the team, newest first, with offset pagination."""
    valid_statuses = {"pending", "claimed", "done", "failed", "cancelled"}
    if status and status not in valid_statuses:
        raise ValueError(f"status must be one of {sorted(valid_statuses)}")
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    limit = min(limit, MAX_TASK_LIMIT)

    params: list = [limit, 0, agent["workspace_id"]]  # $1=limit, $2=offset placeholder, $3=workspace_id
    filters: list[str] = ["t.workspace_id = $3"]
    if status:
        params.append(status)
        filters.append(f"t.status = ${len(params)}::task_status")
    if tags:
        params.append(tags)
        filters.append(f"t.tags && ${len(params)}::text[]")
    params[1] = offset  # replace placeholder
    where = "WHERE " + " AND ".join(filters) if filters else ""

    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT t.id, t.title, t.description, t.status, t.tags, t.priority,
                   t.worker_type, t.deadline, t.claimed_at, t.done_at,
                   creator.handle AS created_by, claimer.handle AS claimed_by,
                   t.created_at
              FROM tasks t
              LEFT JOIN agents creator ON creator.id = t.created_by
              LEFT JOIN agents claimer ON claimer.id = t.claimed_by
              {where}
             ORDER BY t.created_at DESC
             LIMIT $1 OFFSET $2
            """,
            *params,
        )
    return {"tasks": [_task_summary(row) for row in rows]}


async def renew(agent: dict, task_id: str, claim_token: str) -> dict:
    """Renew the lease on a task currently claimed by this agent."""
    tid = _task_uuid(task_id)
    async with db.pool().acquire() as conn:
        renewed = await conn.fetchval(
            """
            UPDATE tasks
               SET claimed_at = now()
             WHERE id = $1
               AND status = 'claimed'
               AND claimed_by = $2
               AND claim_token = $3
               AND workspace_id = $4
            RETURNING claimed_at
            """,
            tid, agent["id"], claim_token, agent["workspace_id"],
        )
    if not renewed:
        raise PermissionError("task is not claimed by this agent or claim_token is invalid")
    return {"ok": True, "claimed_at": renewed.isoformat()}


async def cancel(agent: dict, task_id: str) -> dict:
    """Cancel a pending task created by this agent."""
    tid = _task_uuid(task_id)
    async with db.pool().acquire() as conn:
        cancelled = await conn.fetchval(
            """
            UPDATE tasks
               SET status = 'cancelled', done_at = now()
             WHERE id = $1 AND created_by = $2 AND status = 'pending'
               AND workspace_id = $3
            RETURNING id
            """,
            tid, agent["id"], agent["workspace_id"],
        )
    if not cancelled:
        raise PermissionError("only the creator can cancel a pending task")
    return {"ok": True}


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
                   AND workspace_id = $4
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
            agent["id"], claim_token, tags, agent["workspace_id"],
        )
    else:
        return await conn.fetchrow(
            """
            WITH selected AS (
                SELECT id FROM tasks
                 WHERE status = 'pending'
                   AND workspace_id = $3
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
            agent["id"], claim_token, agent["workspace_id"],
        )


async def _claim_specific(conn, agent: dict, task_id: str, claim_token: str):
    """Claim a specific task by ID. Raises if not found or not pending."""
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise ValueError(f"Invalid task_id: {task_id!r}")

    # Check existence and status with a lock
    row = await conn.fetchrow(
        "SELECT id, status FROM tasks WHERE id = $1 AND workspace_id = $2 FOR UPDATE SKIP LOCKED",
        tid, agent["workspace_id"],
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
               AND workspace_id = $4
            RETURNING id, title, description, priority, tags,
                      deadline, claim_timeout, created_by
        )
        SELECT u.*, COALESCE(a.handle, 'unknown') AS created_by_handle
          FROM updated u
          LEFT JOIN agents a ON a.id = u.created_by
        """,
        agent["id"], claim_token, tid, agent["workspace_id"],
    )


# ── update ─────────────────────────────────────────────────────────────────────

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending":  {"cancelled"},
    "claimed":  {"done", "failed"},
}

_UPDATABLE_FIELDS = {"title", "description", "priority", "tags"}


async def update(
    agent: dict,
    task_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    priority: int | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
) -> dict:
    """
    Update a task owned by this agent.
    - Pending tasks: creator may update title, description, priority, tags, or cancel.
    - Claimed tasks: claimer may mark done/failed.
    - Completed/failed/cancelled tasks are immutable.
    Title and description are guard-scanned before persisting.
    """
    tid = _task_uuid(task_id)

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, status, created_by, claimed_by FROM tasks WHERE id = $1 AND workspace_id = $2 FOR UPDATE",
            tid, agent["workspace_id"],
        )
        if not row:
            raise LookupError(f"Task {task_id} not found")

        current_status = row["status"]
        agent_id = agent["id"]

        if current_status in ("done", "failed", "cancelled"):
            raise ValueError(f"Task {task_id} is already {current_status} — immutable")

        # Determine ownership and valid actions
        if current_status == "pending":
            if row["created_by"] != agent_id:
                raise PermissionError("only the creator can update a pending task")
            if status and status not in _VALID_TRANSITIONS["pending"]:
                raise ValueError(
                    f"Invalid transition: pending → {status}. "
                    f"Allowed: {sorted(_VALID_TRANSITIONS['pending'])}"
                )
        elif current_status == "claimed":
            if row["claimed_by"] != agent_id:
                raise PermissionError("only the claimer can update a claimed task")
            if status and status not in _VALID_TRANSITIONS["claimed"]:
                raise ValueError(
                    f"Invalid transition: claimed → {status}. "
                    f"Allowed: {sorted(_VALID_TRANSITIONS['claimed'])}"
                )
            if any(v is not None for v in (title, description, priority, tags)):
                raise ValueError("cannot update fields on a claimed task — only status")

        # Build SET clause
        set_clauses: list[str] = []
        params: list = []
        idx = 0

        if title is not None:
            idx += 1
            title = await guard.apply(title.strip(), agent_id, "input", "update_task")
            set_clauses.append(f"title = ${idx}")
            params.append(title)
        if description is not None:
            idx += 1
            description = await guard.apply(description, agent_id, "input", "update_task")
            set_clauses.append(f"description = ${idx}")
            params.append(description)
        if priority is not None:
            idx += 1
            set_clauses.append(f"priority = ${idx}")
            params.append(priority)
        if tags is not None:
            idx += 1
            set_clauses.append(f"tags = ${idx}")
            params.append(tags)

        if status is not None:
            idx += 1
            set_clauses.append(f"status = ${idx}::task_status")
            params.append(status)
            if status in ("done", "failed"):
                set_clauses.append(f"done_at = now()")
                set_clauses.append(f"claim_token = NULL")

        if not set_clauses:
            return {"ok": True, "updated_fields": []}

        idx += 1
        params.append(tid)
        idx += 1
        params.append(agent["workspace_id"])

        sql = f"UPDATE tasks SET {', '.join(set_clauses)} WHERE id = ${idx-1} AND workspace_id = ${idx} RETURNING id"
        updated = await conn.fetchval(sql, *params)

    updated_fields = [s.split(" =")[0] for s in set_clauses if s.split(" =")[0] != "claim_token"]
    log.info("Task %s updated by %s: %s", task_id, agent["handle"], updated_fields)
    return {"ok": True, "updated_fields": updated_fields}


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
    """
    if status not in ("done", "failed"):
        raise ValueError(f"status must be 'done' or 'failed', got {status!r}")

    if result is not None:
        result_bytes = len(json.dumps(result).encode())
        if result_bytes > MAX_RESULT_BYTES:
            raise ValueError(
                f"result exceeds maximum size of {MAX_RESULT_BYTES // (1024 * 1024)} MB"
            )
        result_str = await guard.apply(json.dumps(result), agent["id"], "output", "done")
        result = json.loads(result_str)

    tid = _task_uuid(task_id)

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH before AS (
                SELECT status, claimed_by, claim_token
                  FROM tasks
                 WHERE id = $3
                   AND workspace_id = $6
                   FOR UPDATE
            ),
            attempt AS (
                UPDATE tasks
                   SET status      = $1::task_status,
                       done_at     = now(),
                       result      = $2,
                       claim_token = NULL
                 WHERE id = $3
                   AND status = 'claimed'
                   AND claimed_by = $4
                   AND claim_token = $5
                   AND workspace_id = $6
                RETURNING id AS updated_id
            )
            SELECT a.updated_id, b.status AS current_status
              FROM before b
              LEFT JOIN attempt a ON true
            """,
            status, result, tid, agent["id"], claim_token, agent["workspace_id"],
        )
        if row is None:
            raise LookupError(f"Task {task_id} not found")
        if row["updated_id"] is None:
            if row["current_status"] != "claimed":
                raise ValueError(
                    f"Task {task_id} cannot be completed — current status: {row['current_status']}"
                )
            raise PermissionError(
                "task is not claimed by this agent or claim_token does not match"
            )

    log.info("Task %s marked %s by agent %s", task_id, status, agent["handle"])
    return {"ok": True}


def _task_uuid(task_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(task_id)
    except ValueError:
        raise ValueError(f"Invalid task_id: {task_id!r}")


def _task_summary(row) -> dict:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "tags": list(row["tags"]),
        "priority": row["priority"],
        "worker_type": row["worker_type"],
        "deadline": row["deadline"].isoformat() if row["deadline"] else None,
        "claimed_at": row["claimed_at"].isoformat() if row["claimed_at"] else None,
        "done_at": row["done_at"].isoformat() if row["done_at"] else None,
        "created_by": row["created_by"],
        "claimed_by": row["claimed_by"],
        "created_at": row["created_at"].isoformat(),
    }
