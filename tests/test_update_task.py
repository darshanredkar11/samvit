"""Tests for the update_task tool (Phase 2.2)."""
from __future__ import annotations
import uuid
import pytest
from samvit.tools.tasks import update, create, claim, done, cancel


@pytest.mark.asyncio
async def test_update_title(agent_rec):
    created = await create(agent_rec, "original title")
    r = await update(agent_rec, created["task_id"], title="updated title")
    assert r["ok"] is True
    assert "title" in r["updated_fields"]


@pytest.mark.asyncio
async def test_update_description(agent_rec):
    created = await create(agent_rec, "desc task", description="old desc")
    r = await update(agent_rec, created["task_id"], description="new desc")
    assert r["ok"] is True


@pytest.mark.asyncio
async def test_update_priority(agent_rec):
    created = await create(agent_rec, "priority task", priority=1)
    r = await update(agent_rec, created["task_id"], priority=5)
    assert r["ok"] is True


@pytest.mark.asyncio
async def test_update_tags(agent_rec):
    created = await create(agent_rec, "tags task", tags=["frontend"])
    r = await update(agent_rec, created["task_id"], tags=["frontend", "urgent"])
    assert r["ok"] is True


@pytest.mark.asyncio
async def test_update_cancel_pending(agent_rec):
    created = await create(agent_rec, "cancel me")
    r = await update(agent_rec, created["task_id"], status="cancelled")
    assert r["ok"] is True


@pytest.mark.asyncio
async def test_update_done_claimed(agent_rec):
    created = await create(agent_rec, "complete me")
    claimed = await claim(agent_rec, task_id=created["task_id"])
    t = claimed["task"]
    r = await update(agent_rec, t["id"], status="done")
    assert r["ok"] is True


@pytest.mark.asyncio
async def test_update_fail_claimed(agent_rec):
    created = await create(agent_rec, "fail me")
    claimed = await claim(agent_rec, task_id=created["task_id"])
    t = claimed["task"]
    r = await update(agent_rec, t["id"], status="failed")
    assert r["ok"] is True


@pytest.mark.asyncio
async def test_update_wrong_creator_rejected(two_agent_recs):
    creator, other = two_agent_recs
    created = await create(creator, "not yours")
    with pytest.raises(PermissionError, match="only the creator"):
        await update(other, created["task_id"], title="hacked")


@pytest.mark.asyncio
async def test_update_claimed_only_status_change(agent_rec):
    created = await create(agent_rec, "claimed mod")
    claimed = await claim(agent_rec, task_id=created["task_id"])
    t = claimed["task"]
    with pytest.raises(ValueError, match="cannot update fields"):
        await update(agent_rec, t["id"], title="can't edit while claimed")


@pytest.mark.asyncio
async def test_update_invalid_transition_raises(agent_rec):
    created = await create(agent_rec, "bad transition")
    with pytest.raises(ValueError, match="Invalid transition"):
        await update(agent_rec, created["task_id"], status="done")


@pytest.mark.asyncio
async def test_update_nonexistent_task_raises(agent_rec):
    with pytest.raises(LookupError):
        await update(agent_rec, str(uuid.uuid4()), title="ghost")


@pytest.mark.asyncio
async def test_update_immutable_after_done(agent_rec):
    created = await create(agent_rec, "done forever")
    claimed = await claim(agent_rec, task_id=created["task_id"])
    t = claimed["task"]
    await done(agent_rec, t["id"], t["claim_token"])
    with pytest.raises(ValueError, match="immutable"):
        await update(agent_rec, t["id"], title="nope")


@pytest.mark.asyncio
async def test_update_cancelled_immutable(agent_rec):
    created = await create(agent_rec, "cancelled forever")
    await cancel(agent_rec, created["task_id"])
    with pytest.raises(ValueError, match="immutable"):
        await update(agent_rec, created["task_id"], title="nope")


@pytest.mark.asyncio
async def test_update_guard_redacts_secret(agent_rec):
    import os
    os.environ["SAMVIT_GUARD_MODE"] = "redact"
    created = await create(agent_rec, "guard test")
    r = await update(agent_rec, created["task_id"],
                     title="password=SuperSecret123!")
    # Guard should redact — title should not contain the secret
    # We check by fetching the task and examining
    from samvit.tools.tasks import list_tasks
    listed = await list_tasks(agent_rec, status="pending")
    task = next(t for t in listed["tasks"] if t["id"] == created["task_id"])
    assert "SuperSecret123!" not in task["title"]
    assert "[REDACTED:" in task["title"]
    os.environ["SAMVIT_GUARD_MODE"] = "redact"
