"""Tests for the forget tool (Phase 1.2)."""
from __future__ import annotations
import uuid
import pytest
from samvit.tools.memory import forget, remember, recall


@pytest.mark.asyncio
async def test_forget_kv_by_key(agent_rec):
    await remember(agent_rec, "test value", key="forget.test.kv")
    r = await recall(agent_rec, key="forget.test.kv")
    assert len(r["results"]) == 1

    result = await forget(agent_rec, key="forget.test.kv")
    assert result["deleted"] is True

    r = await recall(agent_rec, key="forget.test.kv")
    assert len(r["results"]) == 0


@pytest.mark.asyncio
async def test_forget_kv_miss_returns_false(agent_rec):
    result = await forget(agent_rec, key="no.such.key.exists")
    assert result["deleted"] is False


@pytest.mark.asyncio
async def test_forget_requires_key_or_id(agent_rec):
    with pytest.raises(ValueError, match="key or memory_id"):
        await forget(agent_rec)


@pytest.mark.asyncio
async def test_forget_invalid_uuid_raises(agent_rec):
    with pytest.raises(ValueError, match="Invalid memory_id"):
        await forget(agent_rec, memory_id="not-a-uuid")


@pytest.mark.asyncio
async def test_forget_cross_namespace_rejected(two_agent_recs):
    a1, a2 = two_agent_recs
    with pytest.raises(PermissionError):
        await forget(a2, key="anything", namespace=a1["handle"])


@pytest.mark.asyncio
async def test_forget_global_namespace_allowed(agent_rec):
    await remember(agent_rec, "global secret", key="forget.global.test", namespace="global")
    result = await forget(agent_rec, key="forget.global.test", namespace="global")
    assert result["deleted"] is True
