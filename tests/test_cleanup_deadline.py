"""Tests for cleanup deadline enforcement (Phase 2.4)."""
from __future__ import annotations
import uuid
import pytest
from datetime import datetime, timedelta, timezone

from samvit import db
from samvit.tools.tasks import create
from samvit.cleanup import _cancel_overdue_tasks


@pytest.mark.asyncio
async def test_cancel_overdue_pending_task(agent_rec):
    """A pending task past its deadline should be auto-cancelled."""
    created = await create(agent_rec, "overdue task")
    tid = uuid.UUID(created["task_id"])

    # Set deadline in the past
    async with db.pool().acquire() as conn:
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        await conn.execute(
            "UPDATE tasks SET deadline = $1 WHERE id = $2",
            past, tid,
        )

    count = await _cancel_overdue_tasks()
    assert count >= 1

    async with db.pool().acquire() as conn:
        status = await conn.fetchval("SELECT status FROM tasks WHERE id = $1", tid)
    assert status == "cancelled"


@pytest.mark.asyncio
async def test_future_deadline_not_cancelled(agent_rec):
    """A pending task with a future deadline should not be cancelled."""
    created = await create(agent_rec, "future task")
    tid = uuid.UUID(created["task_id"])

    future = datetime.now(timezone.utc) + timedelta(days=7)
    async with db.pool().acquire() as conn:
        await conn.execute(
            "UPDATE tasks SET deadline = $1 WHERE id = $2",
            future, tid,
        )

    count = await _cancel_overdue_tasks()
    async with db.pool().acquire() as conn:
        status = await conn.fetchval("SELECT status FROM tasks WHERE id = $1", tid)
    assert status == "pending"


@pytest.mark.asyncio
async def test_no_deadline_not_cancelled(agent_rec):
    """A pending task with no deadline should be left alone."""
    created = await create(agent_rec, "no deadline task")
    tid = uuid.UUID(created["task_id"])

    count = await _cancel_overdue_tasks()
    async with db.pool().acquire() as conn:
        status = await conn.fetchval("SELECT status FROM tasks WHERE id = $1", tid)
    assert status == "pending"
