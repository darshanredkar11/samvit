"""E2E: multi-agent coordination — memory → recall → task → claim → done."""
from __future__ import annotations
import pytest
from samvit.tools.memory import recall, remember
from samvit.tools.tasks import claim, create, done, list_tasks


@pytest.mark.asyncio
async def test_multi_agent_share_memory_and_tasks(two_agent_recs):
    """Analyst remembers → Executor recalls → Analyst creates task → Executor does it."""
    analyst, executor = two_agent_recs

    await remember(
        analyst,
        "The best strategy is to prioritize customers with > 100 orders",
        namespace="global",
    )

    recalled = await recall(
        executor,
        query="prioritization strategy",
        namespace="global",
    )
    assert len(recalled["results"]) > 0
    assert "100 orders" in recalled["results"][0]["content"]

    task = await create(
        analyst,
        title="Segment customers by order count",
        description="Use the remembered strategy to segment customers",
        tags=["executor", "test"],
    )
    task_id = task["task_id"]

    claimed = await claim(executor, tags=["executor"])
    assert claimed["task"] is not None
    assert claimed["task"]["id"] == task_id

    await done(
        executor,
        task_id=task_id,
        claim_token=claimed["task"]["claim_token"],
        result={"segments_created": 5, "method": "order_count"},
    )

    all_done = (await list_tasks(analyst, status="done"))["tasks"]
    assert any(t["id"] == task_id for t in all_done)


@pytest.mark.asyncio
async def test_agent_remembers_private_not_shared(two_agent_recs):
    """Agent A's private memory is invisible to Agent B."""
    a1, a2 = two_agent_recs

    await remember(a1, "My secret plan for world domination")
    await remember(a1, "Team standup at 10am", namespace="global")

    found = await recall(a2, query="world domination")
    assert len(found["results"]) == 0

    found = await recall(a2, query="standup", namespace="global")
    assert len(found["results"]) > 0
