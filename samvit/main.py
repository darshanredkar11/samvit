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
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.fastmcp import FastMCP, Context
from pydantic import BaseModel, Field

from samvit import auth, cleanup, db, embeddings, events, rag, codegraph, ratelimit, headroom
from samvit.tools import memory, messaging, tasks
from samvit.integrations.hermes import SamvitMemoryBackend

load_dotenv()

from samvit.logutil import setup as setup_logging
setup_logging()
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
    try:
        embeddings.load_model()   # Decision #14: raises if model unavailable
    except Exception:
        log.warning("Embedding model failed to load — semantic search and RAG will return errors",
                     extra={"action": "install_embeddings"})
    await events.init()
    cleanup_task = asyncio.create_task(cleanup.start())
    ratelimit_cleanup = asyncio.create_task(ratelimit.start_cleanup())

    try:
        async with mcp.session_manager.run():
            yield
    finally:
        # Shutdown
        log.info("Samvit shutting down…")
        cleanup_task.cancel()
        ratelimit_cleanup.cancel()
        await events.close()
        await db.close()


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Samvit", version="0.1.0", lifespan=lifespan)

cors_origins = [
    origin.strip()
    for origin in os.environ.get(
        "SAMVIT_CORS_ORIGINS",
        "http://localhost,http://127.0.0.1",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Mcp-Session-Id"],
    expose_headers=["Mcp-Session-Id"],
)

# ── Auth middleware ────────────────────────────────────────────────────────────

SKIP_AUTH_PATHS = {
    "/health",
    "/ready",
    "/api/metrics",
    "/v1/agents/register",
    "/v1/guard/status",
}

# Admin roles that allow access to admin endpoints
ADMIN_ALLOWED_ROLES = {"admin", "operator", "auditor"}

ADMIN_DEV_MODE = os.environ.get("SAMVIT_ADMIN_DEV_MODE", "").lower() in ("1", "true", "yes")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """
    Validate Bearer token on every request except health + register + admin SPA.
    Admin API endpoints additionally check role and suspension status.
    When SAMVIT_ADMIN_DEV_MODE=true, admin endpoints are freely accessible.
    Decision #10: Authorization value is never forwarded to logs.
    """
    path = request.url.path
    if path in SKIP_AUTH_PATHS or path.startswith("/admin/") or path == "/admin":
        return await call_next(request)

    raw = request.headers.get("Authorization", "")
    if not raw.startswith("Bearer "):
        # Dev mode: allow admin API access without a token
        if ADMIN_DEV_MODE and path.startswith("/v1/admin/"):
            _current_agent.set({
                "id": 0,
                "handle": "dev-admin",
                "role": "admin",
                "provider": "dev",
                "suspended_at": None,
                "workspace_id": None,
            })
            return await call_next(request)
        return _error(401, "Missing or malformed Authorization header")

    token = raw[len("Bearer "):].strip()
    agent = await auth.authenticate(token)
    if not agent:
        return _error(401, "Invalid or expired token")

    # Check suspension
    if agent.get("suspended_at") is not None:
        return _error(403, "Agent is suspended")

    _current_agent.set(agent)

    # Admin endpoints: require admin/operator/auditor role
    if request.url.path.startswith("/v1/admin/"):
        role = agent.get("role", "agent")
        if role not in ADMIN_ALLOWED_ROLES:
            return _error(403, "Insufficient role — admin area")
        # Auditor role is read-only (only GET allowed)
        if role == "auditor" and request.method not in ("GET",):
            return _error(403, "Auditor role is read-only")

    # Rate limiting — check after auth so we have the agent handle
    if request.url.path not in ratelimit.BYPASS_PATHS:
        handle = agent.get("handle", "unknown")
        allowed, retry_after = await ratelimit.get_limiter().check(handle)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"error": f"Rate limit exceeded. Retry after {retry_after}s.", "code": 429},
                headers={"Retry-After": str(retry_after)},
            )

    return await call_next(request)


def _error(code: int, message: str, field: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": message, "code": code}
    if field:
        body["field"] = field
    return JSONResponse(status_code=code, content=body)


@app.exception_handler(PermissionError)
async def permission_error_handler(request: Request, exc: PermissionError) -> JSONResponse:
    return _error(403, str(exc))


# ── HTTP endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "samvit"}


@app.get("/ready")
async def ready():
    """Readiness check: database must respond; event bus may be degraded."""
    try:
        async with db.pool().acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": False},
        )
    return {
        "status": "ready",
        "database": True,
        "event_bus": events.status(),
    }


@app.get("/api/metrics")
async def metrics():
    async with db.pool().acquire() as conn:
        counts = await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM agents) AS agents,
                (SELECT COUNT(*) FROM tasks WHERE status = 'pending') AS pending_tasks,
                (SELECT COUNT(*) FROM semantic_memory) AS memories,
                (SELECT COUNT(*) FROM messages) AS messages
            """
        )
    return {
        "agents": counts["agents"],
        "pending_tasks": counts["pending_tasks"],
        "memories": counts["memories"],
        "messages": counts["messages"],
        "event_bus": events.status(),
    }


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


# ── Task create (HTTP) — used by dispatcher + Hermes cron bridge ──────────────

class TaskCreateRequest(BaseModel):
    title:       str
    description: str | None = None
    tags:        list[str] = Field(default_factory=list)
    priority:    int        = 0
    worker_type: str | None = None
    idempotency_key: str | None = None

@app.post("/v1/tasks", status_code=201)
async def create_task_http(req: TaskCreateRequest):
    agent = _agent()
    return await tasks.create(
        agent, req.title, req.description, req.tags, req.priority,
        req.worker_type, req.idempotency_key,
    )


class ToolCallRequest(BaseModel):
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)


async def _call_tool(agent: dict, tool: str, params: dict[str, Any]) -> dict:
    """Canonical in-process tool dispatcher used by HTTP integrations."""
    log.info("Tool call", extra={"tool": tool, "agent": agent.get("handle"), "params": {k: v for k, v in params.items() if k not in ("claim_token",)}})
    calls = {
        "remember": lambda: memory.remember(agent, **params),
        "recall": lambda: memory.recall(agent, **params),
        "create_task": lambda: tasks.create(agent, **params),
        "list_tasks": lambda: tasks.list_tasks(agent, status=params.get("status"), tags=params.get("tags"), limit=params.get("limit", 50), offset=params.get("offset", 0)),
        "claim": lambda: tasks.claim(agent, **params),
        "renew": lambda: tasks.renew(agent, **params),
        "done": lambda: tasks.done(agent, **params),
        "cancel_task": lambda: tasks.cancel(agent, **params),
        "update_task": lambda: tasks.update(agent, **params),
        "say": lambda: messaging.say(agent, **params),
        "forget": lambda: memory.forget(agent, **params),
        "read": lambda: messaging.read(agent, **params),
        "ingest": lambda: rag.ingest(agent, **params),
        "search_docs": lambda: rag.search_docs(agent, **params),
        "index_code": lambda: codegraph.index_repo(
            agent,
            params["path"],
            params["repo_id"],
            params.get("extensions"),
        ),
        "explore_code": lambda: codegraph.explore_code(agent, **params),
        "who_calls": lambda: codegraph.who_calls(agent, **params),
        "graph_symbol": lambda: codegraph.graph_symbol(agent, **params),
    }
    call = calls.get(tool)
    if call is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool}")
    try:
        result = await call()
        return headroom.compress(tool, result)
    except PermissionError as exc:
        log.info("Tool permission denied", extra={"tool": tool, "error": str(exc)})
        raise HTTPException(status_code=403, detail=str(exc))
    except LookupError as exc:
        log.info("Tool not found", extra={"tool": tool, "error": str(exc)})
        raise HTTPException(status_code=404, detail=str(exc))
    except (TypeError, ValueError) as exc:
        log.info("Tool bad request", extra={"tool": tool, "error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/v1/tools/call")
@app.post("/mcp/call", include_in_schema=False)
async def call_tool(req: ToolCallRequest):
    """HTTP bridge for workers and hooks; MCP clients should use `/mcp`."""
    return await _call_tool(_agent(), req.tool, req.params)


# ── Hermes memory backend HTTP interface ─────────────────────────────────────
# Hermes calls these endpoints when configured with:
#   {"memory": {"backend": "external", "url": "http://localhost:8765/v1/hermes/memory"}}

_hermes_memory = SamvitMemoryBackend()

@app.post("/v1/hermes/memory/store")
async def hermes_memory_store(body: dict):
    ok = await _hermes_memory.store(
        content=body.get("content", ""),
        metadata=body.get("metadata"),
        key=body.get("key"),
    )
    return {"stored": ok}

@app.get("/v1/hermes/memory/search")
async def hermes_memory_search(q: str, limit: int = 5):
    results = await _hermes_memory.search(q, limit)
    return {"results": results}

@app.get("/v1/hermes/memory/get")
async def hermes_memory_get(key: str):
    result = await _hermes_memory.get(key)
    return {"result": result}


@app.get("/v1/hermes/task-exists")
async def hermes_task_exists(tag: str):
    """Real SQL query — does a pending/claimed task with this tag exist?"""
    async with db.AcquireConn() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM tasks WHERE $1 = ANY(tags) AND status IN ('pending', 'claimed') LIMIT 1",
            tag,
        )
        return {"exists": row is not None}


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


# ── Admin API — dashboard and CLI backend ────────────────────────────────────

from samvit import admin as admin_mod


@app.get("/v1/admin/me")
async def admin_me():
    """Return the current agent info for the admin dashboard."""
    a = dict(_agent())
    a.pop("token_hash", None)
    return {"agent": a}


@app.get("/v1/admin/status")
async def admin_status():
    return await admin_mod.get_status(_agent())


@app.get("/v1/admin/agents")
async def admin_list_agents(
    role: str | None = None,
    suspended: bool | None = None,
    limit: int = 50,
    offset: int = 0,
):
    return await admin_mod.list_agents(_agent(), role, suspended, limit, offset)


@app.get("/v1/admin/agents/{handle}")
async def admin_get_agent(handle: str):
    try:
        return await admin_mod.get_agent_detail(_agent(), handle)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/v1/admin/agents", status_code=201)
async def admin_register_agent(req: RegisterRequest, role: str = "agent"):
    try:
        return await admin_mod.register_agent(_agent(), req.handle, req.provider, role)
    except ValueError as exc:
        status = 409 if "already registered" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc))


@app.post("/v1/admin/agents/{handle}/suspend")
async def admin_suspend_agent(handle: str):
    try:
        return await admin_mod.suspend_agent(_agent(), handle)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/v1/admin/agents/{handle}/unsuspend")
async def admin_unsuspend_agent(handle: str):
    try:
        return await admin_mod.unsuspend_agent(_agent(), handle)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/v1/admin/agents/{handle}/role")
async def admin_set_agent_role(handle: str, body: dict):
    role = body.get("role", "")
    try:
        return await admin_mod.set_agent_role(_agent(), handle, role)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/v1/admin/agents/{handle}/rotate")
async def admin_rotate_agent_token(handle: str):
    try:
        return await admin_mod.rotate_agent_token(_agent(), handle)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/v1/admin/tasks")
async def admin_list_tasks(
    status: str | None = None,
    tags: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    tag_list = tags.split(",") if tags else None
    return await admin_mod.admin_list_tasks(
        _agent(), status, tag_list, limit, offset
    )


@app.post("/v1/admin/tasks/{task_id}/release")
async def admin_release_task(task_id: str):
    try:
        return await admin_mod.release_task(_agent(), task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/v1/admin/tasks/{task_id}/cancel")
async def admin_cancel_task(task_id: str):
    try:
        return await admin_mod.admin_cancel_task(_agent(), task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/v1/admin/tasks/release-stale")
async def admin_release_stale():
    return await admin_mod.release_stale_claims(_agent())


@app.get("/v1/admin/guard/violations")
async def admin_guard_violations(
    agent_handle: str | None = None,
    category: str | None = None,
    direction: str | None = None,
    since: str | None = None,
    limit: int = 50,
):
    return await admin_mod.list_guard_violations(
        _agent(), agent_handle, category, direction, since, limit
    )


@app.get("/v1/admin/guard/stats")
async def admin_guard_stats():
    return await admin_mod.guard_stats(_agent())


@app.get("/v1/admin/settings")
async def admin_get_settings():
    return await admin_mod.get_settings(_agent())


@app.post("/v1/admin/settings")
async def admin_update_settings(body: dict):
    try:
        return await admin_mod.update_settings(_agent(), body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/v1/admin/maintenance")
async def admin_maintenance(body: dict):
    enabled = body.get("enabled", False)
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be a boolean")
    return await admin_mod.set_maintenance(_agent(), enabled)


@app.get("/v1/admin/memory/kv/{namespace}")
async def admin_kv_list(namespace: str):
    return await admin_mod.list_kv_namespace(_agent(), namespace)


@app.get("/v1/admin/memory/kv/{namespace}/{key}")
async def admin_kv_get(namespace: str, key: str):
    try:
        return await admin_mod.get_kv_value(_agent(), namespace, key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/v1/admin/hermes/cron-sync")
async def admin_cron_sync():
    return await admin_mod.trigger_cron_sync(_agent())


@app.get("/v1/admin/events/status")
async def admin_events_status():
    return await admin_mod.events_status(_agent())


@app.get("/v1/admin/graph")
async def admin_graph():
    return await admin_mod.get_graph(_agent())


# ── MCP tools ─────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "samvit",
    instructions="Shared memory, task coordination, and messaging for AI teams.",
)


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


@mcp.tool(description="Delete a memory by key (KV) or id (vector). Only your own memories in your namespace or 'global'.")
async def forget(
    ctx: Context,
    key: str | None = None,
    memory_id: str | None = None,
    namespace: str | None = None,
) -> dict:
    agent = _agent()
    try:
        return await memory.forget(agent, key, memory_id, namespace)
    except PermissionError as e:
        return _mcp_error(403, str(e))
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("forget error: %s", e)
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


@mcp.tool(description="Create a task for any connected agent to claim.")
async def create_task(
    title: str,
    ctx: Context,
    description: str | None = None,
    tags: list[str] | None = None,
    priority: int = 0,
    worker_type: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    try:
        return await tasks.create(
            _agent(), title, description, tags, priority, worker_type,
            idempotency_key,
        )
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("create_task error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="List team tasks, optionally filtered by status or tags.")
async def list_tasks(
    ctx: Context,
    status: str | None = None,
    tags: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    try:
        return await tasks.list_tasks(_agent(), status, tags, limit, offset)
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("list_tasks error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="Update a task you own — modify title, description, priority, tags, or transition status (pending→cancelled, claimed→done/failed).")
async def update_task(
    ctx: Context,
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    priority: int | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
) -> dict:
    agent = _agent()
    try:
        return await tasks.update(agent, task_id, title=title, description=description,
                                  priority=priority, tags=tags, status=status)
    except PermissionError as e:
        return _mcp_error(403, str(e))
    except LookupError as e:
        return _mcp_error(404, str(e))
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("update_task error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="Renew the lease on a long-running claimed task.")
async def renew(
    task_id: str,
    claim_token: str,
    ctx: Context,
) -> dict:
    try:
        return await tasks.renew(_agent(), task_id, claim_token)
    except PermissionError as e:
        return _mcp_error(403, str(e))
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("renew error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="Cancel a pending task that you created.")
async def cancel_task(task_id: str, ctx: Context) -> dict:
    try:
        return await tasks.cancel(_agent(), task_id)
    except PermissionError as e:
        return _mcp_error(403, str(e))
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("cancel_task error: %s", e)
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


# ── RAG + Code Graph tools ────────────────────────────────────────────────────

@mcp.tool(description="Ingest a document (PDF, markdown, text, code) — chunk, embed, and store for RAG search. Obsidian [[wikilinks]] are indexed as a knowledge graph.")
async def ingest(
    content: str,
    filename: str,
    ctx: Context,
    description: str | None = None,
    namespace: str = "global",
) -> dict:
    agent = _agent()
    try:
        return await rag.ingest(agent, content, filename, description, namespace)
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("ingest error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="Semantic search across all ingested documents. Returns the most relevant chunks with source attribution. Use instead of reading full files.")
async def search_docs(
    query: str,
    ctx: Context,
    filename_filter: str | None = None,
    namespace: str = "global",
    limit: int = 5,
) -> dict:
    agent = _agent()
    try:
        return await rag.search_docs(agent, query, filename_filter, namespace, limit)
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("search_docs error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="Index a codebase into the knowledge graph — parses all source files, extracts functions/classes/imports/calls. Run once; all agents benefit forever.")
async def index_code(
    path: str,
    repo_id: str,
    ctx: Context,
    extensions: list[str] | None = None,
) -> dict:
    agent = _agent()
    try:
        return await codegraph.index_repo(agent, path, repo_id, extensions)
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("index_code error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="Semantic search over code symbols (functions, classes, methods) by meaning. Returns name, file, line, signature, and docstring. No file reading needed.")
async def explore_code(
    query: str,
    repo_id: str,
    ctx: Context,
    limit: int = 10,
    node_types: list[str] | None = None,
) -> dict:
    try:
        return await codegraph.explore_code(repo_id, query, limit, node_types)
    except ValueError as e:
        return _mcp_error(400, str(e))
    except Exception as e:
        log.error("explore_code error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="Find all callers of a function across the codebase. Answers 'what calls verify_token()?' instantly without grep.")
async def who_calls(
    function_name: str,
    repo_id: str,
    ctx: Context,
) -> dict:
    try:
        return await codegraph.who_calls(repo_id, function_name)
    except Exception as e:
        log.error("who_calls error: %s", e)
        return _mcp_error(500, "Internal error")


@mcp.tool(description="Return the dependency subgraph for a symbol — what it calls, what calls it, what it imports. Returns nodes and edges for graph visualisation.")
async def graph_symbol(
    symbol_name: str,
    repo_id: str,
    ctx: Context,
    depth: int = 2,
) -> dict:
    try:
        return await codegraph.graph_symbol(repo_id, symbol_name, depth)
    except Exception as e:
        log.error("graph_symbol error: %s", e)
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


# ── Serve admin dashboard SPA ────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ADMIN_UI_DIR = None
for candidate in (
    os.path.join(BASE_DIR, "..", "admin-ui", "dist"),   # source tree / editable install
    os.path.join("/app", "admin-ui", "dist"),             # Docker build
):
    candidate = os.path.abspath(candidate)
    if os.path.isdir(os.path.join(candidate, "assets")):
        ADMIN_UI_DIR = candidate
        break

if ADMIN_UI_DIR:
    app.mount(
        "/admin/assets",
        StaticFiles(directory=os.path.join(ADMIN_UI_DIR, "assets")),
        name="admin-ui-assets",
    )

    async def _serve_admin_spa():
        return FileResponse(os.path.join(ADMIN_UI_DIR, "index.html"))

    @app.get("/admin")
    async def admin_spa_root():
        return await _serve_admin_spa()

    @app.get("/admin/{rest_of_path:path}")
    async def admin_spa_catch(rest_of_path: str):
        return await _serve_admin_spa()
else:
    log.warning("admin-ui/dist not found — admin dashboard not mounted")


# ── Mount MCP transports into FastAPI ─────────────────────────────────────────
# Current clients use /mcp. Legacy SSE clients can use /legacy/sse.

app.mount("/legacy", mcp.sse_app())
app.mount("/", mcp.streamable_http_app())
