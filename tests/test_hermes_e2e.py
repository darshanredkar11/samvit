"""E2E: Hermes integration — Samvit as Hermes memory backend.

Uses the tool functions directly rather than the HTTP-based backend
(since test environment uses ASGI transport, not a real HTTP server).
"""
from __future__ import annotations
import os
import pytest
from samvit.tools.memory import recall, remember


@pytest.mark.asyncio
async def test_hermes_semantic_memory_workflow(agent_rec):
    """Hermes stores a semantic memory, searches it."""
    await remember(
        agent_rec,
        content="The user prefers email over SMS for notifications",
        metadata={"source": "user-feedback", "priority": "high"},
        namespace="global",
    )

    results = await recall(
        agent_rec,
        query="How does the user want to be contacted?",
        namespace="global",
    )
    assert len(results["results"]) > 0
    combined = " ".join(r["content"] for r in results["results"]).lower()
    assert "email" in combined or "sms" in combined


@pytest.mark.asyncio
async def test_hermes_kv_store_and_retrieve(agent_rec):
    """Hermes stores and retrieves KV pairs."""
    await remember(
        agent_rec,
        content='{"endpoint": "https://api.example.com", "key": "sk-1234"}',
        key="config.api",
        metadata={"namespace": "hermes"},
    )

    results = await recall(agent_rec, key="config.api")
    assert len(results["results"]) > 0
    assert "endpoint" in results["results"][0]["content"]


@pytest.mark.asyncio
async def test_hermes_kv_delete(agent_rec):
    """Hermes deletes a stored KV entry via forget."""
    from samvit.tools.memory import forget

    await remember(
        agent_rec,
        content="temporary data",
        key="temp.key",
    )

    results = await recall(agent_rec, key="temp.key")
    assert len(results["results"]) > 0

    await forget(agent_rec, key="temp.key")

    results = await recall(agent_rec, key="temp.key")
    assert len(results["results"]) == 0
