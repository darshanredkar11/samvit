"""
In-memory sliding-window rate limiter per agent.

Config via env:
  SAMVIT_RATE_LIMIT         — max requests per window (default 120)
  SAMVIT_RATE_LIMIT_WINDOW  — window in seconds (default 60)

Bypass paths defined in BYPASS_PATHS (health, ready, metrics, guard status).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque

log = logging.getLogger(__name__)

DEFAULT_LIMIT = int(os.environ.get("SAMVIT_RATE_LIMIT", "120"))
DEFAULT_WINDOW = int(os.environ.get("SAMVIT_RATE_LIMIT_WINDOW", "60"))

BYPASS_PATHS = {
    "/health",
    "/ready",
    "/api/metrics",
    "/v1/guard/status",
    "/v1/agents/register",
}


class RateLimiter:
    """
    Sliding-window rate limiter keyed by agent handle.
    Uses a deque per agent; expired timestamps are pruned on every check.
    """

    def __init__(self, limit: int = DEFAULT_LIMIT, window: int = DEFAULT_WINDOW) -> None:
        self.limit = limit
        self.window = window
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, agent_handle: str) -> tuple[bool, int]:
        """
        Check if the agent is within rate limit.
        Returns (allowed: bool, retry_after_seconds: int).
        """
        now = time.monotonic()
        cutoff = now - self.window

        async with self._lock:
            bucket = self._buckets[agent_handle]

            # Prune expired timestamps
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= self.limit:
                retry_after = int(self.window - (now - bucket[0]))
                return False, max(retry_after, 1)

            bucket.append(now)
            return True, 0

    async def cleanup_expired(self) -> None:
        """Periodic cleanup of idle buckets to prevent memory leaks."""
        now = time.monotonic()
        cutoff = now - self.window * 2
        async with self._lock:
            idle = [k for k, v in self._buckets.items() if not v or v[-1] < cutoff]
            for k in idle:
                del self._buckets[k]


# Module-level singleton
_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


async def start_cleanup(interval: int = 300) -> None:
    """Background task to clean up stale rate-limit buckets every 5 min."""
    limiter = get_limiter()
    while True:
        await asyncio.sleep(interval)
        await limiter.cleanup_expired()
