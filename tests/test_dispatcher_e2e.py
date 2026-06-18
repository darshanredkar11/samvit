"""E2E: task lifecycle — create → claim → complete → verify."""
from __future__ import annotations
import pytest
from samvit.tools.tasks import cancel, claim, create, done, list_tasks


@pytest.mark.asyncio
async def test_task_lifecycle_create_claim_done(agent_rec):
    """Full task lifecycle: create → claim → complete."""
    r = await create(agent_rec, "Process user batch", tags=["batch", "test"])
    task_id = r["task_id"]

    claimed = await claim(agent_rec, tags=["test"])
    assert claimed["task"] is not None
    assert claimed["task"]["id"] == task_id

    result = {"emails_extracted": 1042, "errors": 0}
    r_done = await done(agent_rec, task_id, claimed["task"]["claim_token"], result=result)
    assert r_done == {"ok": True}

    tasks = (await list_tasks(agent_rec, status="done"))["tasks"]
    assert any(t["id"] == task_id for t in tasks)


@pytest.mark.asyncio
async def test_task_cancel_by_creator(agent_rec):
    """Creator can cancel a pending task."""
    r = await create(agent_rec, "Cancellable task", tags=["test"])
    task_id = r["task_id"]
    result = await cancel(agent_rec, task_id)
    assert result == {"ok": True}

    tasks = (await list_tasks(agent_rec, status="cancelled"))["tasks"]
    assert any(t["id"] == task_id for t in tasks)


@pytest.mark.asyncio
async def test_task_claim_fail_complete(two_agent_recs):
    """Claimed task can be marked failed with error result."""
    creator, worker = two_agent_recs
    r = await create(creator, "Failing task", tags=["test"])
    task_id = r["task_id"]

    claimed = await claim(worker, tags=["test"])
    assert claimed["task"] is not None

    r_done = await done(worker, task_id, claimed["task"]["claim_token"],
                        result={"error": "timeout"}, status="failed")
    assert r_done == {"ok": True}

    tasks = (await list_tasks(creator, status="failed"))["tasks"]
    assert any(t["id"] == task_id for t in tasks)
