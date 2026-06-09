"""
Database connection pool and migration runner.

Uses asyncpg. Migrations are plain SQL files in migrations/ run in version order.
A Postgres advisory lock prevents concurrent migration runs (multi-replica safe).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)

# Module-level pool — initialised in init(), closed in close()
_pool: asyncpg.Pool | None = None

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
ADVISORY_LOCK_KEY = 20240101  # arbitrary unique int for migration lock


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def init() -> None:
    """Create the connection pool. Call once at startup."""
    global _pool
    dsn = os.environ["DATABASE_URL"]
    _pool = await asyncpg.create_pool(
        dsn,
        min_size=2,
        max_size=10,
        command_timeout=30,
        server_settings={"application_name": "samvit"},
    )
    log.info("Database pool created")


async def close() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        log.info("Database pool closed")


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call db.init() first")
    return _pool


# ── Migration runner ──────────────────────────────────────────────────────────

async def run_migrations() -> None:
    """
    Apply all pending SQL migrations in version order.
    Uses a Postgres advisory lock so only one process runs migrations at a time.
    Each migration file must end with:
        INSERT INTO schema_migrations (version) VALUES (N) ON CONFLICT DO NOTHING;
    """
    async with pool().acquire() as conn:
        # Acquire advisory lock — blocks until available
        await conn.execute("SELECT pg_advisory_lock($1)", ADVISORY_LOCK_KEY)
        try:
            # Ensure migration table exists (bootstrapping)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version    INT PRIMARY KEY,
                    applied_at TIMESTAMPTZ DEFAULT now()
                )
            """)

            applied = {
                row["version"]
                for row in await conn.fetch("SELECT version FROM schema_migrations")
            }

            migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
            for path in migration_files:
                version = int(path.stem.split("_")[0])
                if version in applied:
                    log.debug("Migration %d already applied, skipping", version)
                    continue

                log.info("Applying migration %d: %s", version, path.name)
                sql = path.read_text()
                # Each migration runs atomically
                async with conn.transaction():
                    await conn.execute(sql)

                log.info("Migration %d applied", version)

        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", ADVISORY_LOCK_KEY)
