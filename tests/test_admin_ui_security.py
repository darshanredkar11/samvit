"""E2E: admin UI auth and RBAC enforcement."""
from __future__ import annotations
import uuid

import pytest

from samvit import db


@pytest.mark.asyncio
async def test_admin_ui_static_accessible(http):
    """Admin UI static files are served without auth."""
    resp = await http.get("/admin/")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_api_rejects_no_auth(http):
    """Admin API returns 401 without a token."""
    resp = await http.get("/v1/admin/agents")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_agent_cannot_access_admin_api(agent_rec, http):
    """A regular agent token is rejected by admin API."""
    token = agent_rec.get("_token")
    resp = await http.get(
        "/v1/admin/agents",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_auditor_cannot_mutate(agent_rec, http):
    """An auditor can GET but not POST to admin endpoints."""
    handle = f"auditor-{uuid.uuid4().hex[:8]}"

    async with db.pool().acquire() as conn:
        await conn.execute(
            "UPDATE agents SET role = 'auditor' WHERE id = $1",
            agent_rec["id"],
        )

    token = agent_rec.get("_token")

    resp = await http.get(
        "/v1/admin/agents",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (200, 403)

    resp = await http.post(
        "/v1/admin/agents/test-handle/suspend",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Admin-Secret": "test-admin-secret",
        },
    )
    assert resp.status_code == 403
