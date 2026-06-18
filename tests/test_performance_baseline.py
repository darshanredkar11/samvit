"""Performance baseline: 50 concurrent agents, measure claim latency."""
from __future__ import annotations
import time
import uuid

import pytest

from samvit import auth
from samvit.tools.tasks import claim, create


async def _register_agents(count: int) -> list[dict]:
    """Register `count` test agents and return them."""
    agents = []
    for i in range(count):
        handle = f"perf-{uuid.uuid4().hex[:8]}"
        token_data = await auth.register_agent(handle, "test")
        from samvit import db
        async with db.pool().acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM agents WHERE handle = $1", handle)
        agents.append(dict(row) | {"_token": token_data["token"]})
    return agents


@pytest.mark.asyncio
async def test_50_agents_latency_under_100ms():
    """Target: <100ms per claim at 50 agents."""
    agents = await _register_agents(50)

    creator = agents[0]
    for i in range(500):
        await create(creator, title=f"Benchmark task {i}", tags=["benchmark"])

    start = time.monotonic()
    claimed_count = 0
    for agent in agents:
        r = await claim(agent, tags=["benchmark"])
        if r["task"]:
            claimed_count += 1
    elapsed = time.monotonic() - start

    latency_ms = (elapsed / max(claimed_count, 1)) * 1000
    print(f"\n=== Performance: {claimed_count} claims in {elapsed:.2f}s ===")
    print(f"=== Latency: {latency_ms:.2f}ms per claim ===")

    assert claimed_count > 0, "No tasks were claimed"
    assert latency_ms < 100, (
        f"Latency {latency_ms:.2f}ms exceeds 100ms target"
    )
