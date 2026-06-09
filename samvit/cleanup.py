"""
Background task: release abandoned task claims.

Decision #8 (grace period):
  Cleanup resets tasks where:
    status = 'claimed'
    AND claimed_at + claim_timeout + INTERVAL '5 minutes' < now()

  The 5-minute grace period means an agent that finishes at minute 30+
  still has a window to call done() before the task is released.

Runs every 5 minutes.
"""

from __future__ import annotations

import asyncio
import logging

from samvit import db

log = logging.getLogger(__name__)

INTERVAL_SECONDS = 300   # run every 5 minutes


async def _release_expired_claims() -> int:
    """
    Reset expired claimed tasks back to pending.
    Returns the number of tasks released.
    """
    async with db.pool().acquire() as conn:
        result = await conn.execute(
            """
            UPDATE tasks
               SET status     = 'pending',
                   claimed_by = NULL,
                   claimed_at = NULL,
                   claim_token = NULL
             WHERE status = 'claimed'
               AND claimed_at + claim_timeout + INTERVAL '5 minutes' < now()
            """
        )
    # asyncpg returns "UPDATE N" as a string
    count = int(result.split()[-1])
    if count:
        log.warning("Released %d expired task claim(s) back to pending", count)
    return count


async def start() -> None:
    """Run the cleanup loop forever. Schedule as an asyncio task at startup."""
    log.info("Claim cleanup task started (interval=%ds)", INTERVAL_SECONDS)
    while True:
        try:
            await _release_expired_claims()
        except Exception as exc:
            log.error("Cleanup task error: %s", exc)
        await asyncio.sleep(INTERVAL_SECONDS)
