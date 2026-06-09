"""
Samvit MCP server + HTTP registration endpoints.

Layout:
  - FastAPI lifespan: init DB, run migrations, load embeddings, start Redpanda,
    start cleanup task
  - Auth middleware: validates Bearer token on every request except /health and
    /v1/agents/register; stores agent in _current_agent contextvar
  - HTTP routes: /health, /v1/agents/register, /v1/agents/rotate,
    /v1/admin/agents/{handle}/reset
  - MCP tools: remember, recall, claim, done, say, read
    (mounted at /sse via FastMCP)

Decision #10: Authorization header is NEVER logged — middleware strips it.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP, Context
from pydantic import BaseModel

from samvit import auth, cleanup, db, embeddings, events
from samvit.tools import memory, messaging, tasks

load_dotenv()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

# ── Per-request context variable (set by auth middleware) ─────────────────────
_current_agent: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "current_agent", default=None
)


def _agent() -> dict:
    """Return the authenticated agent for the current request, or raise 401."""
    agent = _current_agent.get()
    if agent is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return agent


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    log.info("Samvit starting up…")
    await db.init()
    await db.run_migrations()
    embeddings.load_model()   # Decision #14: raises if model unavailable
    await events.init()
    cleanup_task = asyncio.create_task(cleanup.start())

    yield

    # Shutdown
    log.info("Samvit shutting down…")
    cleanup_task.cancel()
    await events.close()
    await db.close()


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Samvit", version="0.1.0", lifespan=lifespan)

# ── Auth middleware ────────────────────────────────────────────────────────────

SKIP_AUTH_PATHS = {"/health", "/v1/agents/register"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """
    Validate Bearer token on every request except health + register.
    Decision #10: Authorization value is never forwarded to logs.
    """
    if request.url.path in SKIP_AUTH_PATHS:
        return await call_next(request)

    # Admin endpoints use their own secret validation inside the handler
    if request.url.path.startswith("/v1/admin/"):
        return await call_next(request)

    raw = request.headers.get("Authorization", "")
    if not raw.startswith("Bearer "):
        return _error(401, "Missing or malformed Authorization header")

    token = raw[len("Bearer "):].strip()
    agent = await auth.authenticate(token)
    if not agent:
        return _error(401, "Invalid or expired token")

    _current_agent.set(agent)
    return await call_next(request)


def _error(code: int, message: str, field: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": message, "code": code}
    if field:
        body["field"] = field
    return JSONResponse(status_code=code, content=body)


# ── HTTP endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "samvit"}


class RegisterRequest(BaseModel):
    handle: str
    provider: str


@app.post("/v1/agents/register", status_code=201)
async def register(req: RegisterRequest):
    try:
        result = await auth.register_agent(req.handle, req.provider)
    except ValueError as exc:
        status = 409 if "already registered" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc))
    return result


@app.post("/v1/agents/rotate")
async def rotate():
    agent = _agent()
    try:
        new_token = await auth.rotate_token(agent["id"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"token": new_token}


class AdminResetRequest(BaseModel):
    admin_secret: str


@app.get("/v1/guard/violations")
async def guard_violations(limit: int = 50):
    """
    Return recent guard violations for the authenticated agent.
    Useful for auditing what was blocked/redacted in your sessions.
    """
    agent = _agent()
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, direction, tool, pattern_name, category, severity, snippet, created_at
              FROM guard_violations
             WHERE agent_id = $1
             ORDER BY created_at DESC
             LIMIT $2
            """,
            agent["id"], min(limit, 200),
        )
    return {
        "violations": [
            {
                "id":           str(r["id"]),
                "direction":    r["direction"],
                "tool":         r["tool"],
                "pattern":      r["pattern_name"],
                "category":     r["category"],
                "severity":     r["severity"],
                "snippet":      r["snippet"],
                "at":           r["created_at"].isoformat(),
            }
            for r in rows
        ]
    }


@app.get("/v1/guard/status")
async def guard_status():
    """Return current guard mode — no auth required (it's not sensitive)."""
    from samvit.guard import mode
    return {"mode": mode().value, "patterns": len(__import__("samvit.guard", fromlist=["PATTERNS"]).PATTERNS)}


@app.post("/v1/admin/agents/{handle}/reset")
async def admin_reset(handle: str, req: AdminResetRequest):
    """Decision #12: admin recovery for lost tokens."""
    try:
        new_token = await auth.admin_reset_token(handle, req.admin_secret)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"token": new_token}


# ── MCP tools ─────────────────────────────────────────────────────────────────

mcp = FastMCP("samvit", description="Multi-agent coordination layer")


def _mcp_error(code: int, message: str) -> dict:
    return {"error": message, "code": code}


@mcp.tool(description="Store content as a persistent memory (vector + optional KV).")
async def remember(
    content: str,
    ctx: Context,
    key: str | None = None,
    namespace: str | None = None,
    metadata: dict | None = None,
) -> dict:
    agent = _agent()
    try:
        return await memory.remember(agent, content, key, namespace, metadata)
    except PermissionError as e:
        return _mcp_error(403, str(e))
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("remember error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="Retrieve memories by semantic search or exact key lookup.")
async def recall(
    ctx: Context,
    query: str | None = None,
    key: str | None = None,
    namespace: str | None = None,
    limit: int = 5,
    min_score: float = 0.0,
) -> dict:
    agent = _agent()
    try:
        return await memory.recall(agent, query, key, namespace, limit, min_score)
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("recall error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="Atomically claim the next available task from the queue.")
async def claim(
    ctx: Context,
    tags: list[str] | None = None,
    task_id: str | None = None,
) -> dict:
    agent = _agent()
    try:
        return await tasks.claim(agent, tags, task_id)
    except LookupError as e:
        return _mcp_error(404, str(e))
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("claim error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="Mark a claimed task as done or failed.")
async def done(
    task_id: str,
    claim_token: str,
    ctx: Context,
    result: dict | None = None,
    status: str = "done",
) -> dict:
    agent = _agent()
    try:
        return await tasks.done(agent, task_id, claim_token, result, status)
    except LookupError as e:
        return _mcp_error(404, str(e))
    except PermissionError as e:
        return _mcp_error(403, str(e))
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("done error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="Send a message to an agent or broadcast to all.")
async def say(
    body: str,
    ctx: Context,
    to: str | None = None,
    topic: str | None = None,
    metadata: dict | None = None,
) -> dict:
    agent = _agent()
    try:
        return await messaging.say(agent, body, to, topic, metadata)
    except LookupError as e:
        return _mcp_error(404, str(e))
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("say error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="Read unread messages addressed to you.")
async def read(
    ctx: Context,
    topic: str | None = None,
    from_handle: str | None = None,
    limit: int = 20,
    mark_read: bool = True,
) -> dict:
    agent = _agent()
    try:
        return await messaging.read(agent, topic, from_handle, limit, mark_read)
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("read error: %s", e)
        return _mcp_error(500, "Internal error")


# ── Mount MCP SSE app into FastAPI ────────────────────────────────────────────

app.mount("/", mcp.get_asgi_app())
