"""Tests for claim and done."""
from __future__ import annotations
import asyncio
import uuid
import pytest
from samvit import db
from samvit.tools.tasks import claim, done


async def _insert_task(title: str, tags: list[str] = None, priority: int = 0) -> str:
    async with db.pool().acquire() as conn:
        tid = await conn.fetchval(
            "INSERT INTO tasks (title, tags, priority) VALUES ($1, $2, $3) RETURNING id",
            title, tags or [], priority,
        )
    return str(tid)


@pytest.mark.asyncio
async def test_claim_returns_task(agent_rec):
    tid = await _insert_task(f"task-{uuid.uuid4().hex}")
    r = await claim(agent_rec)
    assert r["task"] is not None
    assert r["task"]["claim_token"]


@pytest.mark.asyncio
async def test_claim_empty_queue_returns_null(agent_rec):
    r = await claim(agent_rec, tags=[f"nonexistent-{uuid.uuid4().hex}"])
    assert r["task"] is None


@pytest.mark.asyncio
async def test_claim_tag_or_filter(agent_rec):
    """Decision #15: OR filter."""
    tid = await _insert_task(f"tagged-{uuid.uuid4().hex}", tags=["backend", "auth"])
    r = await claim(agent_rec, tags=["auth", "frontend"])
    assert r["task"] is not None
    assert r["task"]["id"] == tid


@pytest.mark.asyncio
async def test_claim_specific_task(agent_rec):
    tid = await _insert_task(f"specific-{uuid.uuid4().hex}")
    r = await claim(agent_rec, task_id=tid)
    assert r["task"]["id"] == tid


@pytest.mark.asyncio
async def test_claim_nonexistent_task_raises(agent_rec):
    with pytest.raises(LookupError):
        await claim(agent_rec, task_id=str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_done_happy_path(agent_rec):
    tid = await _insert_task(f"done-{uuid.uuid4().hex}")
    r_claim = await claim(agent_rec, task_id=tid)
    task = r_claim["task"]
    r_done = await done(agent_rec, task["id"], task["claim_token"], result={"ok": True})
    assert r_done == {"ok": True}

    async with db.pool().acquire() as conn:
        status = await conn.fetchval("SELECT status FROM tasks WHERE id = $1", uuid.UUID(tid))
    assert status == "done"


@pytest.mark.asyncio
async def test_done_wrong_token_raises(agent_rec):
    tid = await _insert_task(f"wrong-tok-{uuid.uuid4().hex}")
    task = (await claim(agent_rec, task_id=tid))["task"]
    with pytest.raises(PermissionError):
        await done(agent_rec, task["id"], "wrong-token")


@pytest.mark.asyncio
async def test_done_twice_raises(agent_rec):
    tid = await _insert_task(f"twice-{uuid.uuid4().hex}")
    task = (await claim(agent_rec, task_id=tid))["task"]
    await done(agent_rec, task["id"], task["claim_token"])
    with pytest.raises(ValueError):
        await done(agent_rec, task["id"], task["claim_token"])


@pytest.mark.asyncio
async def test_done_result_too_large_raises(agent_rec):
    """Decision #9: result > 1 MB rejected."""
    tid = await _insert_task(f"big-{uuid.uuid4().hex}")
    task = (await claim(agent_rec, task_id=tid))["task"]
    with pytest.raises(ValueError, match="maximum size"):
        await done(agent_rec, task["id"], task["claim_token"], result={"x": "y" * (1024 * 1024 + 1)})


@pytest.mark.asyncio
async def test_done_invalid_status_raises(agent_rec):
    tid = await _insert_task(f"bad-status-{uuid.uuid4().hex}")
    task = (await claim(agent_rec, task_id=tid))["task"]
    with pytest.raises(ValueError, match="status must be"):
        await done(agent_rec, task["id"], task["claim_token"], status="pending")


@pytest.mark.asyncio
async def test_no_double_claim(two_agent_recs):
    """FOR UPDATE SKIP LOCKED must prevent two agents claiming the same task."""
    a1, a2 = two_agent_recs
    tid = await _insert_task(f"concurrent-{uuid.uuid4().hex}")
    r1, r2 = await asyncio.gather(
        claim(a1, task_id=tid),
        claim(a2, task_id=tid),
    )
    claimed = [r for r in [r1["task"], r2["task"]] if r is not None]
    assert len(claimed) == 1, "Double-claim bug: both agents got the same task"
