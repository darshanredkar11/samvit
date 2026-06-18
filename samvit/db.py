"""
Database connection pool and migration runner.

Uses asyncpg. Migrations are plain SQL files in migrations/ run in version order.
A Postgres advisory lock prevents concurrent migration runs (multi-replica safe).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import TypeVar

import asyncpg

log = logging.getLogger(__name__)

# Module-level pool — initialised in init(), closed in close()
_pool: asyncpg.Pool | None = None

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
if not MIGRATIONS_DIR.exists():
    # Source checkout fallback. Wheels include migrations inside the package.
    MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
ADVISORY_LOCK_KEY = 20240101  # arbitrary unique int for migration lock

T = TypeVar("T")


# ── Retry-capable connection acquisition ──────────────────────────────────────


class AcquireConn:
    """
    Async context manager wrapping pool().acquire() with exponential backoff.
    Drop-in replacement for `async with pool().acquire() as conn:` — use
    `async with db.acquire_conn() as conn:` instead.
    """

    _conn: asyncpg.Connection | None = None

    async def __aenter__(self) -> asyncpg.Connection:
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._conn = await pool().acquire()
                return self._conn
            except Exception as exc:
                last_exc = exc
                if not _is_transient_error(exc):
                    raise
                if attempt < MAX_RETRIES:
                    delay = BASE_DELAY * (2 ** (attempt - 1))
                    log.debug("acquire retry %d/%d in %.2fs: %s",
                              attempt, MAX_RETRIES, delay, exc)
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    async def __aexit__(self, *args) -> None:
        if self._conn:
            await pool().release(self._conn)


# ── Retry helper for transient DB errors ──────────────────────────────────────

MAX_RETRIES = 3
BASE_DELAY = 0.1  # seconds


def _is_transient_error(exc: Exception) -> bool:
    """Return True if the error is likely transient and worth retrying."""
    if isinstance(exc, (asyncpg.exceptions.SerializationError,
                         asyncpg.exceptions.DeadlockDetectedError)):
        return True
    if isinstance(exc, asyncpg.exceptions.ConnectionDoesNotExistError):
        return True
    if isinstance(exc, (OSError, ConnectionError)):
        return True
    return False


async def retry(coro_factory):
    """
    Execute an async DB operation with exponential backoff on transient errors.
    Usage:
        result = await db.retry(lambda: conn.fetch("SELECT 1"))
    """
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            if not _is_transient_error(exc):
                raise
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** (attempt - 1))
                log.debug("DB transient error (attempt %d/%d): %s — retrying in %.2fs",
                          attempt, MAX_RETRIES, exc, delay)
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def _init_conn(conn: asyncpg.Connection) -> None:
    """Register JSONB codec so Python dicts are auto-serialised."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


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
        init=_init_conn,
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
