"""Tests for say and read."""
from __future__ import annotations
import pytest
from samvit.tools.messaging import say, read


@pytest.mark.asyncio
async def test_directed_message(two_agent_recs):
    a1, a2 = two_agent_recs
    await say(a1, "PR is ready for review", to=a2["handle"])
    msgs = (await read(a2))["messages"]
    assert any("PR is ready" in m["body"] for m in msgs)


@pytest.mark.asyncio
async def test_directed_not_visible_to_sender(two_agent_recs):
    a1, a2 = two_agent_recs
    await say(a1, f"only for {a2['handle']}", to=a2["handle"])
    msgs = (await read(a1))["messages"]
    assert not any(a2["handle"] in m["body"] for m in msgs)


@pytest.mark.asyncio
async def test_broadcast_visible_to_all(two_agent_recs):
    a1, a2 = two_agent_recs
    await say(a1, "team: deploy is live", topic="ops")
    msgs = (await read(a2, topic="ops"))["messages"]
    assert any("deploy is live" in m["body"] for m in msgs)


@pytest.mark.asyncio
async def test_peek_does_not_consume(two_agent_recs):
    """Decision §6.6: mark_read=False leaves message unread."""
    a1, a2 = two_agent_recs
    await say(a1, "peek message", to=a2["handle"])
    r1 = (await read(a2, mark_read=False))["messages"]
    r2 = (await read(a2, mark_read=False))["messages"]
    assert any("peek message" in m["body"] for m in r1)
    assert any("peek message" in m["body"] for m in r2)


@pytest.mark.asyncio
async def test_read_consumes_message(two_agent_recs):
    a1, a2 = two_agent_recs
    await say(a1, "consume me", to=a2["handle"])
    r1 = (await read(a2, mark_read=True))["messages"]
    assert any("consume me" in m["body"] for m in r1)
    r2 = (await read(a2))["messages"]
    assert not any("consume me" in m["body"] for m in r2)


@pytest.mark.asyncio
async def test_say_unknown_handle_raises(agent_rec):
    """Decision #4: unknown handle → LookupError (mapped to 404)."""
    with pytest.raises(LookupError):
        await say(agent_rec, "hello", to="totally-nonexistent-xyz")


@pytest.mark.asyncio
async def test_say_empty_body_raises(agent_rec):
    with pytest.raises(ValueError):
        await say(agent_rec, "")


@pytest.mark.asyncio
async def test_say_body_too_large_raises(agent_rec):
    """Decision #9: 64 KB limit."""
    with pytest.raises(ValueError, match="maximum size"):
        await say(agent_rec, "x" * (64 * 1024 + 1))


@pytest.mark.asyncio
async def test_topic_filter(two_agent_recs):
    a1, a2 = two_agent_recs
    await say(a1, "review note", topic="reviews")
    await say(a1, "deploy note", topic="deployments")
    msgs = (await read(a2, topic="reviews", mark_read=False))["messages"]
    assert all(m["topic"] == "reviews" for m in msgs if m["body"] in ("review note",))


@pytest.mark.asyncio
async def test_self_message(agent_rec):
    """Agent can message itself."""
    await say(agent_rec, "self reminder: check logs", to=agent_rec["handle"])
    msgs = (await read(agent_rec))["messages"]
    assert any("self reminder" in m["body"] for m in msgs)


@pytest.mark.asyncio
async def test_http_unauthenticated_rejected(http):
    """HTTP endpoints require a Bearer token."""
    r = await http.post("/v1/agents/rotate")
    assert r.status_code == 401
