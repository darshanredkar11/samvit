"""
Admin tool functions — dashboard and CLI backend.

All functions require the caller agent to have admin/operator/auditor role.
Mutations are logged to admin_audit_log.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from samvit import auth, db

log = logging.getLogger(__name__)

# ── Roles ─────────────────────────────────────────────────────────────────────

VALID_ROLES = {"agent", "operator", "auditor", "admin"}
READ_ONLY_ROLES = {"auditor"}

ADMIN_MUTATIONS = {"admin"}         # can do everything
OPERATOR_MUTATIONS = {"operator"}   # can manage tasks + rotate tokens
MUTATION_ROLES = ADMIN_MUTATIONS | OPERATOR_MUTATIONS


def _require_role(agent: dict, *allowed: str) -> None:
    """Raise PermissionError if agent's role is not in allowed set."""
    role = agent.get("role", "agent")
    if role not in allowed:
        raise PermissionError(
            f"Role '{role}' is not allowed for this action. "
            f"Required one of: {', '.join(sorted(allowed))}"
        )


async def _log(
    admin_handle: str,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Insert a row into admin_audit_log."""
    async with db.pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO admin_audit_log
                (admin_handle, action, target_type, target_id, details)
            VALUES ($1, $2, $3, $4, $5)
            """,
            admin_handle, action, target_type, target_id,
            json.dumps(details) if details else None,
        )


# ── Status ────────────────────────────────────────────────────────────────────

async def get_status(agent: dict) -> dict:
    """Return full system health + aggregated stats."""
    _require_role(agent, *READ_ONLY_ROLES | MUTATION_ROLES)

    async with db.pool().acquire() as conn:
        counts = await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM agents) AS agents,
                (SELECT COUNT(*) FROM agents WHERE role != 'agent') AS admin_agents,
                (SELECT COUNT(*) FROM agents WHERE suspended_at IS NOT NULL) AS suspended,
                (SELECT COUNT(*) FROM tasks WHERE status = 'pending') AS pending_tasks,
                (SELECT COUNT(*) FROM tasks WHERE status = 'claimed') AS claimed_tasks,
                (SELECT COUNT(*) FROM tasks WHERE status IN ('done', 'failed')) AS done_tasks,
                (SELECT COUNT(*) FROM semantic_memory) AS memories,
                (SELECT COUNT(*) FROM messages) AS messages,
                (SELECT COUNT(*) FROM guard_violations WHERE created_at > now() - interval '24 hours') AS violations_24h
            """
        )

    return {
        "agents": {
            "total":       counts["agents"],
            "admins":      counts["admin_agents"],
            "suspended":   counts["suspended"],
        },
        "tasks": {
            "pending":     counts["pending_tasks"],
            "claimed":     counts["claimed_tasks"],
            "done":        counts["done_tasks"],
        },
        "storage": {
            "memories":    counts["memories"],
            "messages":    counts["messages"],
        },
        "guard": {
            "violations_24h": counts["violations_24h"],
            "mode":          __import__("samvit.guard", fromlist=["mode"]).mode().value,
        },
        "events": events.status(),
    }


# ── Agent management ──────────────────────────────────────────────────────────

async def list_agents(
    agent: dict,
    role: str | None = None,
    suspended: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List all agents with task counts."""
    _require_role(agent, *READ_ONLY_ROLES | MUTATION_ROLES)

    limit = min(limit, 200)
    filter_params: list[Any] = []
    count_clauses: list[str] = []
    main_clauses: list[str] = []

    if role:
        filter_params.append(role)
        # COUNT uses its own sequential numbering ($1, $2...)
        count_clauses.append(f"a.role = ${len(filter_params)}")
        # Main query has $1=limit, $2=offset, so filters start at $3
        main_clauses.append(f"a.role = ${len(filter_params) + 2}")
    if suspended is True:
        count_clauses.append("a.suspended_at IS NOT NULL")
        main_clauses.append("a.suspended_at IS NOT NULL")
    elif suspended is False:
        count_clauses.append("a.suspended_at IS NULL")
        main_clauses.append("a.suspended_at IS NULL")

    count_where = "WHERE " + " AND ".join(count_clauses) if count_clauses else ""
    main_where  = "WHERE " + " AND ".join(main_clauses)  if main_clauses  else ""

    async with db.pool().acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM agents a {count_where}",
            *filter_params,
        )
        rows = await conn.fetch(
            f"""
            SELECT a.id, a.handle, a.provider, a.role,
                   a.suspended_at, a.created_at,
                   COALESCE(tc.cnt, 0) AS tasks_created,
                   COALESCE(tcl.cnt, 0) AS tasks_claimed
              FROM agents a
              LEFT JOIN (SELECT created_by, COUNT(*) AS cnt FROM tasks GROUP BY created_by) tc ON tc.created_by = a.id
              LEFT JOIN (SELECT claimed_by, COUNT(*) AS cnt FROM tasks GROUP BY claimed_by) tcl ON tcl.claimed_by = a.id
              {main_where}
              ORDER BY a.created_at DESC
              LIMIT $1 OFFSET $2
            """,
            limit, offset, *filter_params,
        )

    return {
        "agents": [
            {
                "id":             str(r["id"]),
                "handle":         r["handle"],
                "provider":       r["provider"],
                "role":           r["role"],
                "suspended_at":   r["suspended_at"].isoformat() if r["suspended_at"] else None,
                "created_at":     r["created_at"].isoformat(),
                "tasks_created":  r["tasks_created"],
                "tasks_claimed":  r["tasks_claimed"],
            }
            for r in rows
        ],
        "total": total,
    }


async def register_agent(
    admin_agent: dict,
    handle: str,
    provider: str,
    role: str = "agent",
) -> dict:
    """Register a new agent via admin API. Returns one-time token."""
    _require_role(admin_agent, *ADMIN_MUTATIONS)

    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of: {sorted(VALID_ROLES)}")

    result = await auth.register_agent(handle, provider)

    # Update role if different from default
    if role != "agent":
        async with db.pool().acquire() as conn:
            await conn.execute(
                "UPDATE agents SET role = $1 WHERE id = $2",
                role, uuid.UUID(result["agent_id"]),
            )

    await _log(admin_agent["handle"], "register_agent",
               target_type="agent", target_id=result["agent_id"],
               details={"handle": handle, "provider": provider, "role": role})

    return {
        "agent_id": result["agent_id"],
        "handle":   handle,
        "provider": provider,
        "role":     role,
        "token":    result["token"],
    }


async def suspend_agent(admin_agent: dict, handle: str) -> dict:
    """Suspend an agent (prevents all API access)."""
    _require_role(admin_agent, *ADMIN_MUTATIONS)

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, role FROM agents WHERE handle = $1", handle
        )
        if not row:
            raise LookupError(f"Agent '{handle}' not found")
        if row["role"] == "admin":
            raise ValueError("Cannot suspend another admin")

        await conn.execute(
            "UPDATE agents SET suspended_at = now() WHERE handle = $1",
            handle,
        )

    await _log(admin_agent["handle"], "suspend_agent",
               target_type="agent", target_id=str(row["id"]),
               details={"handle": handle})

    return {"ok": True}


async def unsuspend_agent(admin_agent: dict, handle: str) -> dict:
    """Re-activate a suspended agent."""
    _require_role(admin_agent, *ADMIN_MUTATIONS)

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM agents WHERE handle = $1", handle
        )
        if not row:
            raise LookupError(f"Agent '{handle}' not found")

        await conn.execute(
            "UPDATE agents SET suspended_at = NULL WHERE handle = $1",
            handle,
        )

    await _log(admin_agent["handle"], "unsuspend_agent",
               target_type="agent", target_id=str(row["id"]),
               details={"handle": handle})

    return {"ok": True}


async def set_agent_role(admin_agent: dict, handle: str, role: str) -> dict:
    """Change an agent's role."""
    _require_role(admin_agent, *ADMIN_MUTATIONS)

    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of: {sorted(VALID_ROLES)}")

    if admin_agent["handle"] == handle:
        raise ValueError("Cannot change your own role")

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, role FROM agents WHERE handle = $1", handle
        )
        if not row:
            raise LookupError(f"Agent '{handle}' not found")

        old_role = row["role"]
        await conn.execute(
            "UPDATE agents SET role = $1 WHERE handle = $2",
            role, handle,
        )

    await _log(admin_agent["handle"], "set_role",
               target_type="agent", target_id=str(row["id"]),
               details={"handle": handle, "old_role": old_role, "new_role": role})

    return {"ok": True, "role": role}


async def rotate_agent_token(admin_agent: dict, handle: str) -> dict:
    """Rotate an agent's bearer token."""
    _require_role(admin_agent, *ADMIN_MUTATIONS | OPERATOR_MUTATIONS)

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM agents WHERE handle = $1", handle
        )
        if not row:
            raise LookupError(f"Agent '{handle}' not found")
        agent_id = str(row["id"])

    new_token = await auth.rotate_token(agent_id)

    await _log(admin_agent["handle"], "rotate_token",
               target_type="agent", target_id=agent_id,
               details={"handle": handle})

    return {"token": new_token}


async def get_agent_detail(admin_agent: dict, handle: str) -> dict:
    """Return detailed agent info including activity timeline."""
    _require_role(admin_agent, *READ_ONLY_ROLES | MUTATION_ROLES)

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT a.id, a.handle, a.provider, a.role,
                   a.suspended_at, a.created_at,
                   COALESCE(tc.cnt, 0) AS tasks_created,
                   COALESCE(tcl.cnt, 0) AS tasks_claimed,
                   COALESCE(tcd.cnt, 0) AS tasks_completed
              FROM agents a
              LEFT JOIN (SELECT created_by, COUNT(*) AS cnt FROM tasks GROUP BY created_by) tc ON tc.created_by = a.id
              LEFT JOIN (SELECT claimed_by, COUNT(*) AS cnt FROM tasks GROUP BY claimed_by) tcl ON tcl.claimed_by = a.id
              LEFT JOIN (SELECT claimed_by, COUNT(*) AS cnt FROM tasks WHERE status = 'done' GROUP BY claimed_by) tcd ON tcd.claimed_by = a.id
             WHERE a.handle = $1
            """,
            handle,
        )
        if not row:
            raise LookupError(f"Agent '{handle}' not found")

        # Last 20 actions (tasks, messages)
        timeline = await conn.fetch(
            """
            (SELECT 'task_created' AS action_type, id::text AS ref_id,
                    title AS summary, created_at
               FROM tasks WHERE created_by = $1
               ORDER BY created_at DESC LIMIT 10)
            UNION ALL
            (SELECT 'task_claimed' AS action_type, id::text AS ref_id,
                    title AS summary, claimed_at AS created_at
               FROM tasks WHERE claimed_by = $1 AND claimed_at IS NOT NULL
               ORDER BY claimed_at DESC LIMIT 10)
            ORDER BY created_at DESC LIMIT 20
            """,
            row["id"],
        )

    return {
        "id":              str(row["id"]),
        "handle":          row["handle"],
        "provider":        row["provider"],
        "role":            row["role"],
        "suspended_at":    row["suspended_at"].isoformat() if row["suspended_at"] else None,
        "created_at":      row["created_at"].isoformat(),
        "tasks_created":   row["tasks_created"],
        "tasks_claimed":   row["tasks_claimed"],
        "tasks_completed": row["tasks_completed"],
        "timeline": [
            {
                "type":      r["action_type"],
                "ref_id":    r["ref_id"],
                "summary":   r["summary"],
                "at":        r["created_at"].isoformat(),
            }
            for r in timeline
        ],
    }


# ── Admin task management ─────────────────────────────────────────────────────

async def admin_list_tasks(
    agent: dict,
    status: str | None = None,
    tags: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Admin view of all tasks (no ownership restrictions)."""
    _require_role(agent, *READ_ONLY_ROLES | MUTATION_ROLES)

    valid_statuses = {"pending", "claimed", "done", "failed", "cancelled"}
    if status and status not in valid_statuses:
        raise ValueError(f"status must be one of {sorted(valid_statuses)}")
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    limit = min(limit, 200)

    params: list[Any] = [limit, offset]
    filters: list[str] = []
    if status:
        params.append(status)
        filters.append(f"t.status = ${len(params)}::task_status")
    if tags:
        params.append(tags)
        filters.append(f"t.tags && ${len(params)}::text[]")

    where = "WHERE " + " AND ".join(filters) if filters else ""

    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT t.id, t.title, t.description, t.status, t.tags, t.priority,
                   t.worker_type, t.deadline, t.claimed_at, t.done_at,
                   t.result, t.created_at,
                   creator.handle AS created_by, claimer.handle AS claimed_by
              FROM tasks t
              LEFT JOIN agents creator ON creator.id = t.created_by
              LEFT JOIN agents claimer ON claimer.id = t.claimed_by
              {where}
              ORDER BY t.created_at DESC
              LIMIT $1 OFFSET $2
            """,
            *params,
        )

    return {
        "tasks": [
            {
                "id":          str(r["id"]),
                "title":       r["title"],
                "description": r["description"],
                "status":      r["status"],
                "tags":        list(r["tags"]),
                "priority":    r["priority"],
                "worker_type": r["worker_type"],
                "deadline":    r["deadline"].isoformat() if r["deadline"] else None,
                "claimed_at":  r["claimed_at"].isoformat() if r["claimed_at"] else None,
                "done_at":     r["done_at"].isoformat() if r["done_at"] else None,
                "result":      r["result"],
                "created_by":  r["created_by"],
                "claimed_by":  r["claimed_by"],
                "created_at":  r["created_at"].isoformat(),
            }
            for r in rows
        ],
    }


async def release_task(admin_agent: dict, task_id: str) -> dict:
    """Force-release a claimed task back to pending."""
    _require_role(admin_agent, *ADMIN_MUTATIONS | OPERATOR_MUTATIONS)

    tid = uuid.UUID(task_id)
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, status FROM tasks WHERE id = $1 FOR UPDATE",
            tid,
        )
        if not row:
            raise LookupError(f"Task {task_id} not found")
        if row["status"] != "claimed":
            raise ValueError(f"Task {task_id} is not claimed (status={row['status']})")

        await conn.execute(
            """
            UPDATE tasks
               SET status = 'pending',
                   claimed_by = NULL,
                   claimed_at = NULL,
                   claim_token = NULL
             WHERE id = $1
            """,
            tid,
        )

    await _log(admin_agent["handle"], "release_task",
               target_type="task", target_id=task_id)

    return {"ok": True}


async def release_stale_claims(admin_agent: dict) -> dict:
    """Release all expired task claims (same as cleanup)."""
    _require_role(admin_agent, *ADMIN_MUTATIONS | OPERATOR_MUTATIONS)

    async with db.pool().acquire() as conn:
        result = await conn.execute(
            """
            UPDATE tasks
               SET status = 'pending',
                   claimed_by = NULL,
                   claimed_at = NULL,
                   claim_token = NULL
             WHERE status = 'claimed'
               AND claimed_at + claim_timeout + INTERVAL '5 minutes' < now()
            """
        )
    count = int(result.split()[-1])

    if count:
        await _log(admin_agent["handle"], "release_stale_claims",
                   target_type="system", details={"released": count})

    return {"released": count}


async def admin_cancel_task(admin_agent: dict, task_id: str) -> dict:
    """Force-cancel any pending task (admin override)."""
    _require_role(admin_agent, *ADMIN_MUTATIONS | OPERATOR_MUTATIONS)

    tid = uuid.UUID(task_id)
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, status FROM tasks WHERE id = $1 FOR UPDATE",
            tid,
        )
        if not row:
            raise LookupError(f"Task {task_id} not found")
        if row["status"] != "pending":
            raise ValueError(f"Task {task_id} is not pending (status={row['status']})")

        await conn.execute(
            "UPDATE tasks SET status = 'cancelled', done_at = now() WHERE id = $1",
            tid,
        )

    await _log(admin_agent["handle"], "cancel_task",
               target_type="task", target_id=task_id)

    return {"ok": True}


# ── Graph (agent node + edge visualization) ──────────────────────────────────

async def get_graph(agent: dict) -> dict:
    """Return agent nodes, task/message edges, and activity timeline."""
    _require_role(agent, *READ_ONLY_ROLES | MUTATION_ROLES)

    async with db.pool().acquire() as conn:
        agents = await conn.fetch(
            """
            SELECT id, handle, provider, role,
                   suspended_at IS NOT NULL AS suspended,
                   created_at
              FROM agents
             ORDER BY created_at
            """
        )

        tasks = await conn.fetch(
            """
            SELECT t.id, t.title, t.status,
                   creator.handle AS creator_handle,
                   claimer.handle AS claimer_handle,
                   t.created_at
              FROM tasks t
              LEFT JOIN agents creator ON creator.id = t.created_by
              LEFT JOIN agents claimer ON claimer.id = t.claimed_by
             WHERE claimer.handle IS NOT NULL
             ORDER BY t.created_at DESC
             LIMIT 100
            """
        )

        messages = await conn.fetch(
            """
            SELECT m.id, m.topic,
                   left(m.body, 100) AS body_preview,
                   sender.handle AS sender_handle,
                   receiver.handle AS receiver_handle,
                   m.created_at
              FROM messages m
              JOIN agents sender ON sender.id = m.from_agent
              LEFT JOIN agents receiver ON receiver.id = m.to_agent
             WHERE m.to_agent IS NOT NULL
             ORDER BY m.created_at DESC
             LIMIT 100
            """
        )

        timeline = await conn.fetch(
            """
            (SELECT 'agent_created'::text AS action,
                    handle::text AS summary,
                    NULL::uuid AS ref_id,
                    created_at FROM agents)
            UNION ALL
            (SELECT 'task_created'::text, title::text, id, created_at FROM tasks)
            UNION ALL
            (SELECT 'task_claimed'::text, title::text, id, claimed_at
               FROM tasks WHERE claimed_at IS NOT NULL)
            UNION ALL
            (SELECT 'task_done'::text, title::text, id, done_at
               FROM tasks WHERE done_at IS NOT NULL AND status = 'done')
            UNION ALL
            (SELECT 'task_failed'::text, title::text, id, done_at
               FROM tasks WHERE done_at IS NOT NULL AND status = 'failed')
            UNION ALL
            (SELECT CASE WHEN m.to_agent IS NULL THEN 'broadcast'::text
                    ELSE 'message'::text END,
                    left(m.body, 80)::text, m.id, m.created_at
               FROM messages)
            ORDER BY created_at DESC
            LIMIT 50
            """
        )

    return {
        "nodes": [
            {
                "id":         str(r["id"]),
                "handle":     r["handle"],
                "provider":   r["provider"],
                "role":       r["role"],
                "suspended":  r["suspended"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in agents
        ],
        "edges": {
            "tasks": [
                {
                    "id":         str(r["id"]),
                    "title":      r["title"],
                    "status":     r["status"],
                    "source":     r["creator_handle"],
                    "target":     r["claimer_handle"],
                    "created_at": r["created_at"].isoformat(),
                }
                for r in tasks
            ],
            "messages": [
                {
                    "id":         str(r["id"]),
                    "topic":      r["topic"],
                    "preview":    r["body_preview"],
                    "source":     r["sender_handle"],
                    "target":     r["receiver_handle"],
                    "created_at": r["created_at"].isoformat(),
                }
                for r in messages
            ],
        },
        "timeline": [
            {
                "type":    r["action"],
                "summary": r["summary"],
                "ref_id":  str(r["ref_id"]) if r["ref_id"] else None,
                "at":      r["created_at"].isoformat(),
            }
            for r in timeline
        ],
    }


# ─── Guard ─────────────────────────────────────────────────────────────────────

async def list_guard_violations(
    agent: dict,
    agent_handle: str | None = None,
    category: str | None = None,
    direction: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> dict:
    """List guard violations with filters."""
    _require_role(agent, *READ_ONLY_ROLES | MUTATION_ROLES)

    limit = min(limit, 200)
    params: list[Any] = [limit]
    filters: list[str] = []

    if agent_handle:
        params.append(agent_handle.lower())
        filters.append(f"a.handle = ${len(params)}")
    if category:
        params.append(category)
        filters.append(f"gv.category = ${len(params)}")
    if direction:
        params.append(direction)
        filters.append(f"gv.direction = ${len(params)}")
    if since:
        params.append(since)
        filters.append(f"gv.created_at > ${len(params)}::timestamptz")

    where = "WHERE " + " AND ".join(filters) if filters else ""

    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT gv.id, gv.direction, gv.tool, gv.pattern_name, gv.category,
                   gv.severity, gv.snippet, gv.created_at,
                   a.handle AS agent_handle
              FROM guard_violations gv
              JOIN agents a ON a.id = gv.agent_id
              {where}
              ORDER BY gv.created_at DESC
              LIMIT $1
            """,
            *params,
        )

    return {
        "violations": [
            {
                "id":        str(r["id"]),
                "agent":     r["agent_handle"],
                "direction": r["direction"],
                "tool":      r["tool"],
                "pattern":   r["pattern_name"],
                "category":  r["category"],
                "severity":  r["severity"],
                "snippet":   r["snippet"],
                "at":        r["created_at"].isoformat(),
            }
            for r in rows
        ],
    }


async def guard_stats(agent: dict) -> dict:
    """Return aggregated guard violation statistics."""
    _require_role(agent, *READ_ONLY_ROLES | MUTATION_ROLES)

    async with db.pool().acquire() as conn:
        by_pattern = await conn.fetch(
            """
            SELECT category, pattern_name, COUNT(*) AS cnt
              FROM guard_violations
             GROUP BY category, pattern_name
             ORDER BY cnt DESC
             LIMIT 20
            """
        )
        by_agent = await conn.fetch(
            """
            SELECT a.handle, COUNT(*) AS cnt
              FROM guard_violations gv
              JOIN agents a ON a.id = gv.agent_id
             GROUP BY a.handle
             ORDER BY cnt DESC
             LIMIT 10
            """
        )
        total = await conn.fetchval("SELECT COUNT(*) FROM guard_violations")

    return {
        "total":          total,
        "by_pattern":     [dict(r) for r in by_pattern],
        "by_agent":       [dict(r) for r in by_agent],
    }


# ── Settings ──────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS: dict[str, Any] = {
    "guard_mode": "redact",
    "maintenance": False,
}

VALID_SETTING_KEYS = {"guard_mode", "maintenance", "rate_limit", "rate_limit_window"}


async def get_settings(agent: dict) -> dict:
    """Return all system settings."""
    _require_role(agent, *READ_ONLY_ROLES | MUTATION_ROLES)

    async with db.pool().acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM system_settings")
        stored = {r["key"]: r["value"] for r in rows}

    # Merge with defaults
    settings = dict(DEFAULT_SETTINGS)
    settings.update(stored)
    # Ensure guard_mode is string, not JSON object
    if isinstance(settings.get("guard_mode"), str):
        pass
    return settings


async def update_settings(admin_agent: dict, settings: dict) -> dict:
    """Update system settings. Logs each change."""
    _require_role(admin_agent, *ADMIN_MUTATIONS)

    updated: list[str] = []

    async with db.pool().acquire() as conn:
        for key, value in settings.items():
            if key not in VALID_SETTING_KEYS:
                raise ValueError(f"Unknown setting '{key}'. Valid: {sorted(VALID_SETTING_KEYS)}")

            # Validate values
            if key == "guard_mode" and value not in ("block", "redact", "warn", "off"):
                raise ValueError(f"Invalid guard_mode '{value}'")
            if key == "maintenance" and not isinstance(value, bool):
                raise ValueError("maintenance must be a boolean")

            await conn.execute(
                """
                INSERT INTO system_settings (key, value, updated_at)
                VALUES ($1, $2, now())
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = now()
                """,
                key, value,
            )
            updated.append(key)

    if updated:
        await _log(admin_agent["handle"], "update_settings",
                   target_type="setting",
                   details={"updated": updated})

    return {"ok": True, "updated": updated}


# ── Maintenance mode ──────────────────────────────────────────────────────────

async def set_maintenance(admin_agent: dict, enabled: bool) -> dict:
    """Toggle maintenance mode."""
    _require_role(admin_agent, *ADMIN_MUTATIONS)

    async with db.pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO system_settings (key, value, updated_at)
            VALUES ('maintenance', $1, now())
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = now()
            """,
            enabled,
        )

    await _log(admin_agent["handle"], "maintenance",
               target_type="system",
               details={"maintenance": enabled})

    return {"ok": True, "maintenance": enabled}


# ── Memory admin ──────────────────────────────────────────────────────────────

async def list_kv_namespace(agent: dict, namespace: str) -> dict:
    """List all KV entries in a namespace."""
    _require_role(agent, *READ_ONLY_ROLES | MUTATION_ROLES)

    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT kv.key, kv.updated_at, a.handle
              FROM kv_memory kv
              JOIN agents a ON a.id = kv.agent_id
             WHERE kv.namespace = $1
             ORDER BY kv.updated_at DESC
            """,
            namespace,
        )

    return {
        "keys": [
            {
                "key":        r["key"],
                "agent":      r["handle"],
                "updated_at": r["updated_at"].isoformat(),
            }
            for r in rows
        ],
    }


async def get_kv_value(agent: dict, namespace: str, key: str) -> dict:
    """Get a specific KV memory entry."""
    _require_role(agent, *READ_ONLY_ROLES | MUTATION_ROLES)

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT kv.key, kv.value, kv.updated_at, a.handle
              FROM kv_memory kv
              JOIN agents a ON a.id = kv.agent_id
             WHERE kv.namespace = $1 AND kv.key = $2
            """,
            namespace, key,
        )
        if not row:
            raise LookupError(f"Key '{key}' not found in namespace '{namespace}'")

    return {
        "key":        row["key"],
        "value":      row["value"],
        "agent":      row["handle"],
        "updated_at": row["updated_at"].isoformat(),
    }


# ── Hermes admin ──────────────────────────────────────────────────────────────

async def trigger_cron_sync(admin_agent: dict) -> dict:
    """Trigger Hermes cron bridge sync."""
    _require_role(admin_agent, *ADMIN_MUTATIONS | OPERATOR_MUTATIONS)

    from samvit.integrations.hermes import HermesCronBridge
    bridge = HermesCronBridge()
    result = await bridge.sync_to_samvit()

    await _log(admin_agent["handle"], "cron_sync",
               target_type="system",
               details=result)

    return {"ok": True, **result}


# ── Events / Redpanda admin ───────────────────────────────────────────────────
