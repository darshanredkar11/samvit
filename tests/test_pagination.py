"""Tests for list_tasks offset pagination (Phase 2.3)."""
from __future__ import annotations
import pytest
from samvit.tools.tasks import create, list_tasks


@pytest.mark.asyncio
async def test_list_tasks_offset_skips_first(agent_rec):
    t1 = await create(agent_rec, "paginate A")
    t2 = await create(agent_rec, "paginate B")

    all_tasks = (await list_tasks(agent_rec))["tasks"]
    assert len(all_tasks) >= 2

    offset_1 = (await list_tasks(agent_rec, limit=1, offset=1))["tasks"]
    assert len(offset_1) == 1
    assert offset_1[0]["id"] == all_tasks[1]["id"]


@pytest.mark.asyncio
async def test_list_tasks_offset_beyond_end(agent_rec):
    tasks = (await list_tasks(agent_rec, offset=9999))["tasks"]
    assert tasks == []


@pytest.mark.asyncio
async def test_list_tasks_negative_offset_raises(agent_rec):
    with pytest.raises(ValueError, match="offset must be >= 0"):
        await list_tasks(agent_rec, offset=-1)


@pytest.mark.asyncio
async def test_list_tasks_limit_and_offset_together(agent_rec):
    ids = []
    for i in range(3):
        r = await create(agent_rec, f"batch {i}")
        ids.append(r["task_id"])

    page = (await list_tasks(agent_rec, limit=2, offset=0))["tasks"]
    assert len(page) == 2
    assert page[0]["id"] == ids[2]  # newest first

    page2 = (await list_tasks(agent_rec, limit=2, offset=2))["tasks"]
    assert len(page2) >= 1
