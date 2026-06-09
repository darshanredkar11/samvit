"""
Shared pytest fixtures.

Tests call tool functions directly — FastMCP uses SSE/JSON-RPC, not REST,
so there are no /mcp/tools/... HTTP routes to POST to.

HTTP endpoints (register, rotate, health, admin reset) are tested via
ASGI client against FastAPI.

Spin up real infra first:
    docker compose up -d postgres redpanda
Then run:
    pytest
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("DATABASE_URL", "postgresql://samvit:samvit@localhost:5432/samvit")
os.environ.setdefault("REDPANDA_BROKERS", "localhost:9092")
os.environ.setdefault("SAMVIT_ADMIN_SECRET", "test-admin-secret")

from samvit import db, embeddings, events
from samvit.main import app


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _startup():
    """Full startup once per test session (mirrors lifespan)."""
    await db.init()
    await db.run_migrations()
    embeddings.load_model()
    await events.init()
    yield
    await events.close()
    await db.close()


@pytest_asyncio.fixture
async def http():
    """Unauthenticated ASGI client — for registration and health checks."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def agent_rec():
    """
    Register a unique agent in the DB.
    Returns the raw agent dict as stored (id, handle, provider).
    """
    handle = f"test-{uuid.uuid4().hex[:8]}"
    token_data = await __import__("samvit.auth", fromlist=["register_agent"]).register_agent(
        handle, "test"
    )
    # Fetch the full agent row that tools expect
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM agents WHERE handle = $1", handle)
    return dict(row) | {"_token": token_data["token"]}


@pytest_asyncio.fixture
async def two_agent_recs():
    """Two separate agent records for messaging/concurrent tests."""
    import samvit.auth as auth_mod
    agents = []
    for _ in range(2):
        handle = f"test-{uuid.uuid4().hex[:8]}"
        await auth_mod.register_agent(handle, "test")
        async with db.pool().acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM agents WHERE handle = $1", handle)
        agents.append(dict(row))
    return agents
