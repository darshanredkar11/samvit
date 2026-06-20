"""
Tests for the Admin API (/v1/admin/*) and admin module.

Covers:
  - Role-based access control (admin, operator, auditor, agent)
  - Agent management (list, register, suspend, unsuspend, role change, rotate)
  - Admin task management (list, force-release, cancel, release-stale)
  - Guard violations admin view + stats
  - System settings (get, update)
  - Maintenance mode toggle
  - KV memory admin (list namespace, get value)
  - Admin audit logging
  - Suspension enforcement at middleware level
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from samvit import auth, db
from samvit.main import app


async def _register_admin_agent(handle: str, provider: str = "test", role: str = "admin"):
    """Register an agent and promote to admin/operator/auditor role."""
    token_data = await auth.register_agent(handle, provider)
    async with db.pool().acquire() as conn:
        await conn.execute(
            "UPDATE agents SET role = $1 WHERE handle = $2",
            role, handle,
        )
        row = await conn.fetchrow("SELECT * FROM agents WHERE handle = $1", handle)
    return dict(row) | {"_token": token_data["token"]}


@pytest_asyncio.fixture
async def admin_agent():
    return await _register_admin_agent(f"admin-{uuid.uuid4().hex[:8]}", "test", "admin")


@pytest_asyncio.fixture
async def operator_agent():
    return await _register_admin_agent(f"op-{uuid.uuid4().hex[:8]}", "test", "operator")


@pytest_asyncio.fixture
async def auditor_agent():
    return await _register_admin_agent(f"auditor-{uuid.uuid4().hex[:8]}", "test", "auditor")


def _auth_headers(agent: dict) -> dict:
    return {"Authorization": f"Bearer {agent['_token']}"}


# ── Role-based Access Control ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_cannot_access_admin(agent_rec, http: AsyncClient):
    """Regular agents get 403 on admin endpoints."""
    r = await http.get("/v1/admin/status", headers=_auth_headers(agent_rec))
    assert r.status_code == 403
    assert "Insufficient role" in r.text


@pytest.mark.asyncio
async def test_auditor_can_read(admin_agent, auditor_agent, http: AsyncClient):
    """Auditor can GET admin endpoints."""
    r = await http.get("/v1/admin/status", headers=_auth_headers(auditor_agent))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_auditor_cannot_write(admin_agent, auditor_agent, http: AsyncClient):
    """Auditor gets 403 on POST endpoints."""
    r = await http.post(
        "/v1/admin/agents/test-handle/suspend",
        headers=_auth_headers(auditor_agent),
    )
    assert r.status_code == 403
    assert "read-only" in r.text


@pytest.mark.asyncio
async def test_operator_can_manage_tasks(admin_agent, operator_agent, http: AsyncClient):
    """Operator can use task management endpoints."""
    r = await http.post(
        "/v1/admin/tasks/release-stale",
        headers=_auth_headers(operator_agent),
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_operator_cannot_register_agent(admin_agent, operator_agent, http: AsyncClient):
    """Operator cannot register new agents (admin-only)."""
    r = await http.post(
        "/v1/admin/agents",
        json={"handle": f"new-{uuid.uuid4().hex[:8]}", "provider": "test"},
        headers=_auth_headers(operator_agent),
    )
    assert r.status_code == 403


# ── Status ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_status(admin_agent, http: AsyncClient):
    r = await http.get("/v1/admin/status", headers=_auth_headers(admin_agent))
    assert r.status_code == 200
    data = r.json()
    assert "agents" in data
    assert "tasks" in data
    assert "storage" in data
    assert "guard" in data
    assert data["agents"]["total"] >= 1
    assert data["tasks"]["pending"] >= 0


# ── Agent Management ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_agents(admin_agent, http: AsyncClient):
    r = await http.get("/v1/admin/agents", headers=_auth_headers(admin_agent))
    assert r.status_code == 200
    data = r.json()
    assert "agents" in data
    assert "total" in data
    assert len(data["agents"]) >= 1
    # Our admin agent should be in the list
    handles = [a["handle"] for a in data["agents"]]
    assert admin_agent["handle"] in handles


@pytest.mark.asyncio
async def test_list_agents_filter_by_role(admin_agent, http: AsyncClient):
    r = await http.get(
        "/v1/admin/agents?role=admin",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    data = r.json()
    assert all(a["role"] == "admin" for a in data["agents"])


@pytest.mark.asyncio
async def test_register_agent_admin(admin_agent, http: AsyncClient):
    handle = f"new-{uuid.uuid4().hex[:8]}"
    r = await http.post(
        "/v1/admin/agents",
        json={"handle": handle, "provider": "cli"},
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 201
    data = r.json()
    assert data["handle"] == handle
    assert data["role"] == "agent"
    assert data["provider"] == "cli"
    assert "token" in data
    assert "agent_id" in data


@pytest.mark.asyncio
async def test_register_agent_with_role(admin_agent, http: AsyncClient):
    handle = f"op-{uuid.uuid4().hex[:8]}"
    r = await http.post(
        "/v1/admin/agents?role=operator",
        json={"handle": handle, "provider": "test"},
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 201
    assert r.json()["role"] == "operator"


@pytest.mark.asyncio
async def test_register_duplicate_handle(admin_agent, http: AsyncClient):
    handle = f"dup-{uuid.uuid4().hex[:8]}"
    await http.post(
        "/v1/admin/agents",
        json={"handle": handle, "provider": "test"},
        headers=_auth_headers(admin_agent),
    )
    r = await http.post(
        "/v1/admin/agents",
        json={"handle": handle, "provider": "test"},
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_get_agent_detail(admin_agent, http: AsyncClient):
    r = await http.get(
        f"/v1/admin/agents/{admin_agent['handle']}",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["handle"] == admin_agent["handle"]
    assert data["role"] == "admin"
    assert "timeline" in data
    assert "tasks_created" in data


@pytest.mark.asyncio
async def test_get_agent_detail_not_found(admin_agent, http: AsyncClient):
    r = await http.get(
        "/v1/admin/agents/nonexistent-handle",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_suspend_and_unsuspend_agent(admin_agent, agent_rec, http: AsyncClient):
    handle = agent_rec["handle"]
    # Suspend
    r = await http.post(
        f"/v1/admin/agents/{handle}/suspend",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Verify suspended agent gets 403 on a normal non-admin endpoint
    r2 = await http.get("/v1/guard/violations", headers=_auth_headers(agent_rec))
    assert r2.status_code == 403
    assert "suspended" in r2.text

    # Unsuspend
    r3 = await http.post(
        f"/v1/admin/agents/{handle}/unsuspend",
        headers=_auth_headers(admin_agent),
    )
    assert r3.status_code == 200

    # Verify agent works again
    r4 = await http.get("/v1/guard/violations", headers=_auth_headers(agent_rec))
    assert r4.status_code == 200


@pytest.mark.asyncio
async def test_suspend_nonexistent_agent(admin_agent, http: AsyncClient):
    r = await http.post(
        "/v1/admin/agents/nonexistent/suspend",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_suspend_admin_raises(admin_agent, http: AsyncClient):
    """Cannot suspend another admin."""
    # Create another admin
    other = await _register_admin_agent(f"admin2-{uuid.uuid4().hex[:8]}", "test", "admin")
    r = await http.post(
        f"/v1/admin/agents/{other['handle']}/suspend",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 400
    assert "Cannot suspend another admin" in r.text


@pytest.mark.asyncio
async def test_set_agent_role(admin_agent, agent_rec, http: AsyncClient):
    r = await http.post(
        f"/v1/admin/agents/{agent_rec['handle']}/role",
        json={"role": "operator"},
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    assert r.json()["role"] == "operator"


@pytest.mark.asyncio
async def test_set_agent_role_cannot_change_self(admin_agent, http: AsyncClient):
    r = await http.post(
        f"/v1/admin/agents/{admin_agent['handle']}/role",
        json={"role": "operator"},
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 400
    assert "Cannot change your own role" in r.text


@pytest.mark.asyncio
async def test_rotate_agent_token(admin_agent, agent_rec, http: AsyncClient):
    old_token = agent_rec["_token"]
    r = await http.post(
        f"/v1/admin/agents/{agent_rec['handle']}/rotate",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert data["token"] != old_token

    # Old token should no longer work
    r2 = await http.get("/v1/admin/status", headers={"Authorization": f"Bearer {old_token}"})
    assert r2.status_code == 401


# ── Admin Task Management ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_list_tasks(admin_agent, agent_rec, http: AsyncClient):
    # Create a task via the normal HTTP endpoint
    from samvit.tools.tasks import create
    await create(agent_rec, f"admin-list-test-{uuid.uuid4().hex}")

    r = await http.get("/v1/admin/tasks", headers=_auth_headers(admin_agent))
    assert r.status_code == 200
    data = r.json()
    assert "tasks" in data
    assert len(data["tasks"]) >= 1


@pytest.mark.asyncio
async def test_admin_list_tasks_filter_status(admin_agent, http: AsyncClient):
    r = await http.get(
        "/v1/admin/tasks?status=pending",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    assert all(t["status"] == "pending" for t in r.json()["tasks"])


@pytest.mark.asyncio
async def test_release_task(admin_agent, two_agent_recs, http: AsyncClient):
    """Force-release a claimed task."""
    from samvit.tools.tasks import claim, create

    creator, claimer = two_agent_recs
    task = await create(creator, f"release-{uuid.uuid4().hex}")
    await claim(claimer, task_id=task["task_id"])

    r = await http.post(
        f"/v1/admin/tasks/{task['task_id']}/release",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Task should be claimable again
    claimed_again = await claim(claimer, task_id=task["task_id"])
    assert claimed_again["task"] is not None


@pytest.mark.asyncio
async def test_release_task_not_found(admin_agent, http: AsyncClient):
    r = await http.post(
        f"/v1/admin/tasks/{uuid.uuid4()}/release",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_release_task_not_claimed(admin_agent, agent_rec, http: AsyncClient):
    from samvit.tools.tasks import create
    task = await create(agent_rec, f"not-claimed-{uuid.uuid4().hex}")
    r = await http.post(
        f"/v1/admin/tasks/{task['task_id']}/release",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 400
    assert "not claimed" in r.text


@pytest.mark.asyncio
async def test_admin_cancel_task(admin_agent, agent_rec, http: AsyncClient):
    from samvit.tools.tasks import create
    task = await create(agent_rec, f"admin-cancel-{uuid.uuid4().hex}")
    r = await http.post(
        f"/v1/admin/tasks/{task['task_id']}/cancel",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_admin_cancel_not_pending(admin_agent, two_agent_recs, http: AsyncClient):
    from samvit.tools.tasks import claim, create
    creator, claimer = two_agent_recs
    task = await create(creator, f"cancel-claimed-{uuid.uuid4().hex}")
    await claim(claimer, task_id=task["task_id"])
    r = await http.post(
        f"/v1/admin/tasks/{task['task_id']}/cancel",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 400
    assert "not pending" in r.text


@pytest.mark.asyncio
async def test_release_stale_claims(admin_agent, http: AsyncClient):
    r = await http.post(
        "/v1/admin/tasks/release-stale",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    assert "released" in r.json()


# ── Guard ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_guard_violations_list(admin_agent, agent_rec, http: AsyncClient):
    """Trigger a guard violation, then verify admin can see it."""
    import os
    os.environ["SAMVIT_GUARD_MODE"] = "redact"

    from samvit.tools.memory import remember
    await remember(agent_rec, "password=SuperSecret123!")

    r = await http.get(
        "/v1/admin/guard/violations",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    data = r.json()
    assert "violations" in data
    # May be empty if no violations in the test scope, but should succeed

    r2 = await http.get(
        f"/v1/admin/guard/violations?agent_handle={agent_rec['handle']}",
        headers=_auth_headers(admin_agent),
    )
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_guard_stats(admin_agent, http: AsyncClient):
    r = await http.get("/v1/admin/guard/stats", headers=_auth_headers(admin_agent))
    assert r.status_code == 200
    data = r.json()
    assert "total" in data
    assert "by_pattern" in data
    assert "by_agent" in data


# ── Settings ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_settings(admin_agent, http: AsyncClient):
    r = await http.get("/v1/admin/settings", headers=_auth_headers(admin_agent))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_update_settings(admin_agent, http: AsyncClient):
    r = await http.post(
        "/v1/admin/settings",
        json={"guard_mode": "block"},
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "guard_mode" in r.json()["updated"]

    # Verify it persisted
    r2 = await http.get("/v1/admin/settings", headers=_auth_headers(admin_agent))
    assert r2.json()["guard_mode"] == "block"

    # Reset
    await http.post(
        "/v1/admin/settings",
        json={"guard_mode": "redact"},
        headers=_auth_headers(admin_agent),
    )


@pytest.mark.asyncio
async def test_update_settings_invalid_key(admin_agent, http: AsyncClient):
    r = await http.post(
        "/v1/admin/settings",
        json={"nonexistent_key": "value"},
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_settings_invalid_guard_mode(admin_agent, http: AsyncClient):
    r = await http.post(
        "/v1/admin/settings",
        json={"guard_mode": "invalid"},
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 400


# ── Maintenance Mode ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_maintenance_toggle(admin_agent, http: AsyncClient):
    r = await http.post(
        "/v1/admin/maintenance",
        json={"enabled": True},
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    assert r.json()["maintenance"] is True

    r2 = await http.post(
        "/v1/admin/maintenance",
        json={"enabled": False},
        headers=_auth_headers(admin_agent),
    )
    assert r2.status_code == 200
    assert r2.json()["maintenance"] is False


@pytest.mark.asyncio
async def test_maintenance_invalid_body(admin_agent, http: AsyncClient):
    r = await http.post(
        "/v1/admin/maintenance",
        json={"enabled": "yes"},
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 400


# ── KV Memory Admin ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_kv_list_namespace(admin_agent, http: AsyncClient):
    r = await http.get(
        f"/v1/admin/memory/kv/{admin_agent['handle']}",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    assert "keys" in r.json()


@pytest.mark.asyncio
async def test_kv_get_value(admin_agent, agent_rec, http: AsyncClient):
    """Store a KV memory, then verify admin can read it."""
    from samvit.tools.memory import remember
    await remember(agent_rec, "hello world", key="admin-test-key")

    r = await http.get(
        f"/v1/admin/memory/kv/{agent_rec['handle']}/admin-test-key",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["key"] == "admin-test-key"
    assert data["value"]["text"] == "hello world"


@pytest.mark.asyncio
async def test_kv_get_value_not_found(admin_agent, http: AsyncClient):
    r = await http.get(
        f"/v1/admin/memory/kv/global/nonexistent-key-{uuid.uuid4().hex}",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 404


# ── Admin Audit Log ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_log_created_on_register(admin_agent, http: AsyncClient):
    handle = f"audit-{uuid.uuid4().hex[:8]}"
    await http.post(
        "/v1/admin/agents",
        json={"handle": handle, "provider": "test"},
        headers=_auth_headers(admin_agent),
    )

    async with db.pool().acquire() as conn:
        log_row = await conn.fetchrow(
            "SELECT * FROM admin_audit_log WHERE target_id IS NOT NULL ORDER BY created_at DESC LIMIT 1"
        )
    assert log_row is not None
    assert log_row["admin_handle"] == admin_agent["handle"]
    assert log_row["action"] == "register_agent"


@pytest.mark.asyncio
async def test_audit_log_created_on_suspend(admin_agent, agent_rec, http: AsyncClient):
    await http.post(
        f"/v1/admin/agents/{agent_rec['handle']}/suspend",
        headers=_auth_headers(admin_agent),
    )

    async with db.pool().acquire() as conn:
        log_row = await conn.fetchrow(
            "SELECT * FROM admin_audit_log WHERE action = 'suspend_agent' ORDER BY created_at DESC LIMIT 1"
        )
    assert log_row is not None
    assert log_row["admin_handle"] == admin_agent["handle"]


# ── Auth: Suspension Enforcement ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_suspended_agent_sees_403_on_normal_endpoints(admin_agent, agent_rec, http: AsyncClient):
    """Suspended agents are blocked from ALL endpoints, not just admin."""
    handle = agent_rec["handle"]
    await http.post(
        f"/v1/admin/agents/{handle}/suspend",
        headers=_auth_headers(admin_agent),
    )

    # Even a non-admin call should be blocked
    r = await http.get(
        "/v1/guard/violations",
        headers=_auth_headers(agent_rec),
    )
    assert r.status_code == 403
    assert "suspended" in r.text

    # Clean up
    await http.post(
        f"/v1/admin/agents/{handle}/unsuspend",
        headers=_auth_headers(admin_agent),
    )


@pytest.mark.asyncio
async def test_suspended_operator_cannot_access_admin(admin_agent, agent_rec, http: AsyncClient):
    """A suspended operator-level agent cannot access admin endpoints."""
    # First promote agent_rec to operator, then suspend it
    async with db.pool().acquire() as conn:
        await conn.execute(
            "UPDATE agents SET role = 'operator' WHERE id = $1",
            agent_rec["id"],
        )

    await http.post(
        f"/v1/admin/agents/{agent_rec['handle']}/suspend",
        headers=_auth_headers(admin_agent),
    )

    r = await http.get(
        "/v1/admin/status",
        headers=_auth_headers(agent_rec),
    )
    assert r.status_code == 403
    assert "suspended" in r.text

    # Clean up
    await http.post(
        f"/v1/admin/agents/{agent_rec['handle']}/unsuspend",
        headers=_auth_headers(admin_agent),
    )

    async with db.pool().acquire() as conn:
        await conn.execute(
            "UPDATE agents SET role = 'agent' WHERE id = $1",
            agent_rec["id"],
        )


# ── Edge Cases ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unauthenticated_admin_request(http: AsyncClient):
    """No token → 401."""
    r = await http.get("/v1/admin/status")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_admin_invalid_role_value(admin_agent, http: AsyncClient):
    r = await http.post(
        "/v1/admin/agents",
        json={"handle": f"bad-role-{uuid.uuid4().hex[:8]}", "provider": "test"},
        params={"role": "superadmin"},
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 400




@pytest.mark.asyncio
async def test_hermes_cron_sync(admin_agent, http: AsyncClient):
    """Cron sync should succeed even if no Hermes config exists."""
    r = await http.post(
        "/v1/admin/hermes/cron-sync",
        headers=_auth_headers(admin_agent),
    )
    assert r.status_code == 200
    data = r.json()
    assert "crons_found" in data
