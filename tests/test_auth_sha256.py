"""Tests for SHA-256 fast token lookup (Phase 5)."""
from __future__ import annotations
import pytest
from samvit.auth import _token_sha256, authenticate, register_agent, rotate_token
from samvit import db


def test_token_sha256_deterministic():
    h1 = _token_sha256("samvit_test_token_123")
    h2 = _token_sha256("samvit_test_token_123")
    assert h1 == h2
    assert len(h1) == 64  # hex-encoded SHA-256


def test_token_sha256_different_tokens():
    h1 = _token_sha256("samvit_token_a")
    h2 = _token_sha256("samvit_token_b")
    assert h1 != h2


@pytest.mark.asyncio
async def test_authenticate_uses_sha256(agent_rec):
    token = agent_rec["_token"]
    agent = await authenticate(token)
    assert agent is not None
    assert agent["handle"] == agent_rec["handle"]

    # Verify SHA-256 was stored
    sha256 = _token_sha256(token)
    async with db.pool().acquire() as conn:
        stored_sha = await conn.fetchval(
            "SELECT token_hash_sha256 FROM agents WHERE handle = $1",
            agent_rec["handle"],
        )
    assert stored_sha == sha256


@pytest.mark.asyncio
async def test_register_stores_sha256():
    import uuid
    handle = f"sha-test-{uuid.uuid4().hex[:8]}"
    result = await register_agent(handle, "test")
    token = result["token"]

    sha256 = _token_sha256(token)
    async with db.pool().acquire() as conn:
        stored_sha = await conn.fetchval(
            "SELECT token_hash_sha256 FROM agents WHERE handle = $1",
            handle,
        )
    assert stored_sha == sha256


@pytest.mark.asyncio
async def test_rotate_updates_sha256(agent_rec):
    old_sha = _token_sha256(agent_rec["_token"])
    new_token = await rotate_token(agent_rec["id"])

    new_sha = _token_sha256(new_token)
    assert new_sha != old_sha

    async with db.pool().acquire() as conn:
        stored_sha = await conn.fetchval(
            "SELECT token_hash_sha256 FROM agents WHERE id = $1",
            agent_rec["id"],
        )
    assert stored_sha == new_sha


@pytest.mark.asyncio
async def test_authenticate_invalid_token(agent_rec):
    result = await authenticate("samvit_totally_fake_token_12345")
    assert result is None
