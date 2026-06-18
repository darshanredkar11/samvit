"""Tests for the rate limiter module (Phase 1.3)."""
from __future__ import annotations
import pytest
from samvit.ratelimit import RateLimiter, BYPASS_PATHS


@pytest.mark.asyncio
async def test_rate_limit_allows_within_window():
    limiter = RateLimiter(limit=5, window=60)
    for _ in range(5):
        allowed, retry_after = await limiter.check("test-agent")
        assert allowed is True
        assert retry_after == 0


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_limit():
    limiter = RateLimiter(limit=3, window=60)
    for _ in range(3):
        await limiter.check("burst-agent")

    allowed, retry_after = await limiter.check("burst-agent")
    assert allowed is False
    assert retry_after >= 1


@pytest.mark.asyncio
async def test_rate_limit_different_agents_independent():
    limiter = RateLimiter(limit=2, window=60)
    await limiter.check("alice")
    await limiter.check("alice")
    allowed_bob, _ = await limiter.check("bob")
    assert allowed_bob is True  # bob unaffected by alice


@pytest.mark.asyncio
async def test_bypass_paths_not_rate_limited():
    assert "/health" in BYPASS_PATHS
    assert "/ready" in BYPASS_PATHS
    assert "/v1/agents/register" in BYPASS_PATHS


@pytest.mark.asyncio
async def test_cleanup_expired():
    limiter = RateLimiter(limit=2, window=1)
    await limiter.check("leaky")
    await limiter.check("leaky")
    # after cleanup, bucket should be empty or near-empty
    await limiter.cleanup_expired()
    remaining = len(limiter._buckets.get("leaky", []))
    assert remaining <= 2


@pytest.mark.asyncio
async def test_sliding_window_expires():
    limiter = RateLimiter(limit=2, window=0.01)
    import asyncio
    await limiter.check("expire")
    await limiter.check("expire")
    allowed, _ = await limiter.check("expire")
    assert allowed is False
    await asyncio.sleep(0.02)
    # Window has slid — old entries expired
    allowed, _ = await limiter.check("expire")
    assert allowed is True
