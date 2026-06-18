"""Tests for Headroom compression wrapper (Phase 4)."""
from __future__ import annotations
import pytest
from samvit.headroom import compress, HAS_HEADROOM, COMPRESSED_TOOLS
from samvit.tools.tasks import list_tasks


def test_compress_returns_original_without_headroom():
    """Without headroom-ai installed, compress is a no-op."""
    if HAS_HEADROOM:
        pytest.skip("headroom-ai is installed — testing real compression")
    result = {"results": [{"content": "hello", "score": 0.95}]}
    compressed = compress("recall", result)
    assert compressed is result


def test_compress_untouched_tool():
    result = {"ok": True}
    compressed = compress("create_task", result)
    assert compressed is result


def test_compress_small_result_unchanged():
    result = {"results": [{"x": "short"}]}
    compressed = compress("recall", result)
    assert compressed is result


def test_compress_unknown_tool():
    result = {"x": "y" * 1000}
    compressed = compress("nonexistent_tool", result)
    assert compressed is result


def test_compressed_tools_set():
    assert "recall" in COMPRESSED_TOOLS
    assert "search_docs" in COMPRESSED_TOOLS
    assert "explore_code" in COMPRESSED_TOOLS
    assert "read" in COMPRESSED_TOOLS
    assert "list_tasks" in COMPRESSED_TOOLS
    assert "who_calls" in COMPRESSED_TOOLS
    assert "graph_symbol" in COMPRESSED_TOOLS


@pytest.mark.asyncio
async def test_list_tasks_compression_applied_in_dispatcher(agent_rec):
    """Verify that list_tasks result goes through compress in the dispatcher."""
    from samvit.tools.tasks import create
    await create(agent_rec, "headroom test task")

    # Call through the MCP tool to see if compression wraps the result
    from samvit.main import _call_tool
    agent = agent_rec
    result = await _call_tool(agent, "list_tasks", {"limit": 5, "offset": 0})

    # If headroom is installed and result is large enough, it's compressed
    data = result
    if HAS_HEADROOM and data.get("_compressed"):
        assert data["_tool"] == "list_tasks"
        assert data["_original_size"] > 0
    else:
        assert "tasks" in data
