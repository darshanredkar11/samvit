"""Tests for remember and recall."""
from __future__ import annotations
import pytest
from samvit.tools.memory import remember, recall


@pytest.mark.asyncio
async def test_remember_vector_and_recall(agent_rec):
    await remember(agent_rec, "JWT tokens expire in 24 hours, refresh via /api/refresh")
    r = await recall(agent_rec, query="JWT token expiry")
    assert len(r["results"]) >= 1
    assert "JWT" in r["results"][0]["content"]
    assert r["results"][0]["score"] > 0.5


@pytest.mark.asyncio
async def test_remember_kv_and_recall_by_key(agent_rec):
    await remember(agent_rec, "Stripe secret is in .env as STRIPE_SECRET", key="stripe.location")
    r = await recall(agent_rec, key="stripe.location")
    assert len(r["results"]) == 1
    assert "Stripe" in r["results"][0]["content"]


@pytest.mark.asyncio
async def test_kv_miss_returns_empty_not_404(agent_rec):
    """Decision #5."""
    r = await recall(agent_rec, key="definitely-not-stored")
    assert r == {"results": []}


@pytest.mark.asyncio
async def test_empty_content_rejected(agent_rec):
    with pytest.raises(ValueError, match="empty"):
        await remember(agent_rec, "")


@pytest.mark.asyncio
async def test_content_too_large_rejected(agent_rec):
    """Decision #9: 32 KB limit."""
    with pytest.raises(ValueError, match="maximum size"):
        await remember(agent_rec, "x" * (32 * 1024 + 1))


@pytest.mark.asyncio
async def test_kv_upsert_updates_value(agent_rec):
    await remember(agent_rec, "old value", key="upsert-key")
    await remember(agent_rec, "new value", key="upsert-key")
    r = await recall(agent_rec, key="upsert-key")
    assert "new value" in r["results"][0]["content"]


@pytest.mark.asyncio
async def test_global_namespace_shared(two_agent_recs):
    """Decision #3: any agent can write/read global namespace."""
    a1, a2 = two_agent_recs
    await remember(a1, "shared arch decision for postgres sharding", namespace="global")
    r = await recall(a2, query="postgres sharding", namespace="global")
    assert any("shared arch" in res["content"] for res in r["results"])


@pytest.mark.asyncio
async def test_private_namespace_isolated(two_agent_recs):
    """Agent A's private memories not visible to Agent B in their own namespace."""
    a1, a2 = two_agent_recs
    await remember(a1, "agent one private secret note")
    # a2 searches their own namespace — should not find a1's note
    r = await recall(a2, query="private secret note")
    assert not any("agent one" in res["content"] for res in r["results"])


@pytest.mark.asyncio
async def test_cross_namespace_write_rejected(two_agent_recs):
    """Decision #3: cannot write to another agent's private namespace."""
    a1, a2 = two_agent_recs
    with pytest.raises(PermissionError):
        await remember(a2, "sneaky write", namespace=a1["handle"])


@pytest.mark.asyncio
async def test_recall_requires_query_or_key(agent_rec):
    with pytest.raises(ValueError):
        await recall(agent_rec)
