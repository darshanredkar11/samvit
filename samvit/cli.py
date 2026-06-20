"""Command-line entry point for running, checking, and administering Samvit."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="samvit",
        description="Shared coordination server for mixed AI-agent teams.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── serve ──────────────────────────────────────────────────────────────
    serve = sub.add_parser("serve", help="Run the Samvit server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8765, type=int)
    serve.add_argument("--reload", action="store_true")

    # ── migrate ────────────────────────────────────────────────────────────
    migrate = sub.add_parser("migrate", help="Run pending DB migrations and exit")
    migrate.add_argument("--url", help="DATABASE_URL override")

    # ── register (public) ──────────────────────────────────────────────────
    register = sub.add_parser("register", help="Register an agent (public endpoint)")
    register.add_argument("handle")
    register.add_argument("--provider", required=True)
    register.add_argument("--url", default="http://127.0.0.1:8765")

    # ── rotate-token (admin-secret) ────────────────────────────────────────
    rotate = sub.add_parser("rotate-token", help="Rotate an agent's bearer token via admin secret")
    rotate.add_argument("handle")
    rotate.add_argument("--admin-secret", required=True)
    rotate.add_argument("--url", default="http://127.0.0.1:8765")

    # ── connect ────────────────────────────────────────────────────────────
    connect = sub.add_parser("connect", help="Configure AI clients to use this Samvit server")
    connect.add_argument("--url", default="http://localhost:8765", help="Samvit server URL")
    connect.add_argument("--token", required=True, help="Agent bearer token (from 'samvit register')")
    connect.add_argument(
        "--client",
        metavar="NAME",
        help="Client(s) to configure: claude, codex, cursor, kiro, opencode, antigravity, or 'all'. Default: auto-detect.",
    )

    # ── doctor ─────────────────────────────────────────────────────────────
    doctor = sub.add_parser("doctor", help="Check a Samvit deployment")
    doctor.add_argument("--url", default="http://127.0.0.1:8765")

    # ── admin ──────────────────────────────────────────────────────────────
    admin = sub.add_parser("admin", help="Admin operations (requires admin token)")
    admin.add_argument("--url", default="http://127.0.0.1:8765")
    admin.add_argument("--token", default="", help="Admin bearer token (omit for dev mode)")
    admin_sub = admin.add_subparsers(dest="admin_command", required=True)

    # admin agents
    agents = admin_sub.add_parser("agents", help="Agent management")
    agents_sub = agents.add_subparsers(dest="agents_command", required=True)
    _ = agents_sub.add_parser("list", help="List all agents")
    detail = agents_sub.add_parser("detail", help="Show agent detail with timeline")
    detail.add_argument("handle")
    reg = agents_sub.add_parser("register", help="Register a new agent")
    reg.add_argument("handle")
    reg.add_argument("--provider", required=True)
    reg.add_argument("--role", default="agent", choices=["agent", "operator", "auditor", "admin"])
    suspend = agents_sub.add_parser("suspend", help="Suspend an agent")
    suspend.add_argument("handle")
    unsuspend = agents_sub.add_parser("unsuspend", help="Unsuspend an agent")
    unsuspend.add_argument("handle")
    set_role = agents_sub.add_parser("set-role", help="Change an agent's role")
    set_role.add_argument("handle")
    set_role.add_argument("role", choices=["agent", "operator", "auditor", "admin"])
    rotate = agents_sub.add_parser("rotate", help="Rotate an agent's token (admin)")
    rotate.add_argument("handle")

    # admin tasks
    tasks = admin_sub.add_parser("tasks", help="Task management")
    tasks_sub = tasks.add_subparsers(dest="tasks_command", required=True)
    task_list = tasks_sub.add_parser("list", help="List all tasks")
    task_list.add_argument("--status", choices=["pending", "claimed", "done", "failed", "cancelled"])
    task_list.add_argument("--tag")
    task_release = tasks_sub.add_parser("release", help="Force-release a claimed task")
    task_release.add_argument("task_id")
    task_cancel = tasks_sub.add_parser("cancel", help="Cancel a pending task")
    task_cancel.add_argument("task_id")
    tasks_sub.add_parser("release-stale", help="Release all stale claims")

    # admin guard
    guard = admin_sub.add_parser("guard", help="Guard violation inspection")
    guard_sub = guard.add_subparsers(dest="guard_command", required=True)
    gv = guard_sub.add_parser("violations", help="List guard violations")
    gv.add_argument("--agent", help="Filter by agent handle")
    gv.add_argument("--category")
    guard_sub.add_parser("stats", help="Guard violation statistics")

    # admin settings
    settings = admin_sub.add_parser("settings", help="System settings")
    settings_sub = settings.add_subparsers(dest="settings_command", required=True)
    settings_sub.add_parser("get", help="View current settings")
    set_s = settings_sub.add_parser("set", help="Update a setting")
    set_s.add_argument("key", choices=["guard_mode", "rate_limit", "rate_limit_window"])
    set_s.add_argument("value")

    # admin maintenance
    maint = admin_sub.add_parser("maintenance", help="Toggle maintenance mode")
    maint.add_argument("state", choices=["on", "off"])

    # admin memory
    memory = admin_sub.add_parser("memory", help="KV memory inspection")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    kv_list = memory_sub.add_parser("kv-list", help="List KV entries in a namespace")
    kv_list.add_argument("namespace")
    kv_get = memory_sub.add_parser("kv-get", help="Get a specific KV value")
    kv_get.add_argument("namespace")
    kv_get.add_argument("key")

    # admin hermes
    hermes = admin_sub.add_parser("hermes", help="Hermes integration management")
    hermes_sub = hermes.add_subparsers(dest="hermes_command", required=True)
    hermes_sub.add_parser("cron-sync", help="Trigger Hermes cron bridge sync")

    return parser


# ── samvit connect ──────────────────────────────────────────────────────────


def _merge_json(path: Path, keys: list[str], value: dict) -> None:
    data = json.loads(path.read_text()) if path.exists() else {}
    node = data
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _client_claude(url: str, token: str) -> str:
    mcp_url = f"{url}/mcp"
    if shutil.which("claude"):
        r = subprocess.run(
            ["claude", "mcp", "add", "--transport", "http", "samvit", mcp_url,
             "--header", f"Authorization: Bearer {token}"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return "added via `claude mcp add`"
    cfg = Path.home() / ".claude.json"
    _merge_json(cfg, ["mcpServers", "samvit"], {
        "type": "http", "url": mcp_url,
        "headers": {"Authorization": f"Bearer {token}"},
    })
    return str(cfg)


def _client_codex(url: str, token: str) -> str:
    cfg = Path.home() / ".codex" / "config.toml"
    mcp_url = f"{url}/mcp"
    existing = cfg.read_text() if cfg.exists() else ""
    if "samvit" in existing:
        return f"already in {cfg}"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    with cfg.open("a") as f:
        f.write(
            f'\n[[mcp_servers]]\nname = "samvit"\ntype = "http"\n'
            f'url = "{mcp_url}"\n'
            f'headers = {{ Authorization = "Bearer {token}" }}\n'
        )
    return str(cfg)


def _client_cursor(url: str, token: str) -> str:
    cfg = Path.home() / ".cursor" / "mcp.json"
    _merge_json(cfg, ["mcpServers", "samvit"], {
        "url": f"{url}/mcp", "headers": {"Authorization": f"Bearer {token}"},
    })
    return str(cfg)


def _client_kiro(url: str, token: str) -> str:
    # ponytail: path from kiro.dev/docs/mcp — verify on next Kiro release
    cfg = Path.home() / ".kiro" / "settings" / "mcp.json"
    _merge_json(cfg, ["mcpServers", "samvit"], {
        "url": f"{url}/mcp", "headers": {"Authorization": f"Bearer {token}"},
    })
    return str(cfg)


def _client_opencode(url: str, token: str) -> str:
    cfg = Path.home() / ".config" / "opencode" / "config.json"
    _merge_json(cfg, ["mcp", "samvit"], {
        "type": "remote", "url": f"{url}/mcp",
        "headers": {"Authorization": f"Bearer {token}"},
    })
    return str(cfg)


def _client_antigravity(url: str, token: str) -> str:
    cfg = Path.home() / ".antigravity" / "mcp.json"
    _merge_json(cfg, ["mcpServers", "samvit"], {
        "url": f"{url}/mcp", "headers": {"Authorization": f"Bearer {token}"},
    })
    return str(cfg)


# (display_name, detect_fn, write_fn)
_CLIENTS: dict[str, tuple[str, Any, Any]] = {
    "claude":      ("Claude Code",  lambda: shutil.which("claude") or (Path.home() / ".claude").exists(),                    _client_claude),
    "codex":       ("Codex CLI",    lambda: shutil.which("codex") or (Path.home() / ".codex").exists(),                      _client_codex),
    "cursor":      ("Cursor",       lambda: shutil.which("cursor") or (Path.home() / ".cursor").exists(),                    _client_cursor),
    "kiro":        ("Kiro",         lambda: shutil.which("kiro") or (Path.home() / ".kiro").exists(),                        _client_kiro),
    "opencode":    ("OpenCode",     lambda: shutil.which("opencode") or (Path.home() / ".config" / "opencode").exists(),     _client_opencode),
    "antigravity": ("Antigravity",  lambda: shutil.which("antigravity") or (Path.home() / ".antigravity").exists(),          _client_antigravity),
}


def _run_connect(args: argparse.Namespace) -> int:
    url = args.url.rstrip("/")
    token = args.token

    if args.client == "all":
        targets = list(_CLIENTS)
    elif args.client:
        targets = [c.strip() for c in args.client.split(",")]
        unknown = [c for c in targets if c not in _CLIENTS]
        if unknown:
            print(f"Unknown: {', '.join(unknown)}. Supported: {', '.join(_CLIENTS)}", file=sys.stderr)
            return 1
    else:
        targets = [k for k, (_, detect, _) in _CLIENTS.items() if detect()]
        if not targets:
            print("No known AI clients detected. Use --client NAME or --client all.")
            print(f"Supported: {', '.join(_CLIENTS)}")
            return 0
        print(f"Detected: {', '.join(_CLIENTS[t][0] for t in targets)}")

    print()
    ok = True
    for key, (display, _, write) in _CLIENTS.items():
        if key not in targets:
            print(f"  - {display:<16} skipped")
            continue
        try:
            result = write(url, token)
            print(f"  ✓ {display:<16} {result}")
        except Exception as exc:
            print(f"  ✗ {display:<16} failed: {exc}")
            ok = False

    print("\nRestart your clients to pick up the Samvit MCP server.")
    return 0 if ok else 1


# ── HTTP helpers ────────────────────────────────────────────────────────────


async def _get(url: str, path: str, token: str = "") -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=15, headers=headers) as c:
        r = await c.get(f"{url.rstrip('/')}{path}")
    if r.is_error:
        raise SystemExit(f"Error {r.status_code}: {r.text}")
    return r.json()


async def _post(url: str, path: str, body: dict | None = None, token: str = "") -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=15, headers=headers) as c:
        r = await c.post(f"{url.rstrip('/')}{path}", json=body)
    if r.is_error:
        raise SystemExit(f"Error {r.status_code}: {r.text}")
    return r.json()


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


# ── Command handlers (public) ──────────────────────────────────────────────


async def _register(args: argparse.Namespace) -> int:
    r = await _post(args.url, "/v1/agents/register", {"handle": args.handle, "provider": args.provider})
    _print(r)
    return 0


async def _rotate_token(args: argparse.Namespace) -> int:
    r = await _post(
        args.url,
        f"/v1/admin/agents/{args.handle}/reset",
        {"admin_secret": args.admin_secret},
    )
    _print(r)
    return 0


async def _doctor(args: argparse.Namespace) -> int:
    base = args.url.rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        health = await client.get(f"{base}/health")
        ready = await client.get(f"{base}/ready")
    report = {
        "url": base,
        "live": health.status_code == 200,
        "ready": ready.status_code == 200,
        "details": ready.json() if ready.content else {},
    }
    _print(report)
    return 0 if report["live"] and report["ready"] else 1


# ── Command handlers (admin) ────────────────────────────────────────────────


async def _admin_agents(args: argparse.Namespace) -> int:
    base = args.url
    tok = args.token
    cmd = args.agents_command
    if cmd == "list":
        r = await _get(base, "/v1/admin/agents", tok)
        _print(r)
    elif cmd == "detail":
        r = await _get(base, f"/v1/admin/agents/{args.handle}", tok)
        _print(r)
    elif cmd == "register":
        r = await _post(
            base, "/v1/admin/agents",
            {"handle": args.handle, "provider": args.provider, "role": args.role},
            tok,
        )
        _print(r)
    elif cmd == "suspend":
        r = await _post(base, f"/v1/admin/agents/{args.handle}/suspend", token=tok)
        _print(r)
    elif cmd == "unsuspend":
        r = await _post(base, f"/v1/admin/agents/{args.handle}/unsuspend", token=tok)
        _print(r)
    elif cmd == "set-role":
        r = await _post(base, f"/v1/admin/agents/{args.handle}/role", {"role": args.role}, tok)
        _print(r)
    elif cmd == "rotate":
        r = await _post(base, f"/v1/admin/agents/{args.handle}/rotate", token=tok)
        _print(r)
    return 0


async def _admin_tasks(args: argparse.Namespace) -> int:
    base = args.url
    tok = args.token
    cmd = args.tasks_command
    if cmd == "list":
        path = "/v1/admin/tasks?limit=100"
        if getattr(args, "status", None):
            path += f"&status={args.status}"
        r = await _get(base, path, tok)
        _print(r)
    elif cmd == "release":
        r = await _post(base, f"/v1/admin/tasks/{args.task_id}/release", token=tok)
        _print(r)
    elif cmd == "cancel":
        r = await _post(base, f"/v1/admin/tasks/{args.task_id}/cancel", token=tok)
        _print(r)
    elif cmd == "release-stale":
        r = await _post(base, "/v1/admin/tasks/release-stale", token=tok)
        _print(r)
    return 0


async def _admin_guard(args: argparse.Namespace) -> int:
    base = args.url
    tok = args.token
    cmd = args.guard_command
    if cmd == "violations":
        path = "/v1/admin/guard/violations?limit=100"
        if getattr(args, "agent", None):
            path += f"&agent_handle={args.agent}"
        if getattr(args, "category", None):
            path += f"&category={args.category}"
        r = await _get(base, path, tok)
        _print(r)
    elif cmd == "stats":
        r = await _get(base, "/v1/admin/guard/stats", tok)
        _print(r)
    return 0


async def _admin_settings(args: argparse.Namespace) -> int:
    base = args.url
    tok = args.token
    cmd = args.settings_command
    if cmd == "get":
        r = await _get(base, "/v1/admin/settings", tok)
        _print(r)
    elif cmd == "set":
        r = await _post(base, "/v1/admin/settings", {args.key: args.value}, tok)
        _print(r)
    return 0


async def _admin_maintenance(args: argparse.Namespace) -> int:
    enabled = args.state == "on"
    r = await _post(args.url, "/v1/admin/maintenance", {"enabled": enabled}, args.token)
    _print(r)
    return 0


async def _admin_memory(args: argparse.Namespace) -> int:
    base = args.url
    tok = args.token
    cmd = args.memory_command
    if cmd == "kv-list":
        r = await _get(base, f"/v1/admin/memory/kv/{args.namespace}", tok)
        _print(r)
    elif cmd == "kv-get":
        r = await _get(base, f"/v1/admin/memory/kv/{args.namespace}/{args.key}", tok)
        _print(r)
    return 0


async def _admin_hermes(args: argparse.Namespace) -> int:
    if args.hermes_command == "cron-sync":
        r = await _post(args.url, "/v1/admin/hermes/cron-sync", token=args.token)
        _print(r)
    return 0


async def _admin_events(args: argparse.Namespace) -> int:
    if args.events_command == "status":
        r = await _get(args.url, "/v1/admin/events/status", args.token)
        _print(r)
    return 0


# ── Dispatch ────────────────────────────────────────────────────────────────


ADMIN_DISPATCH = {
    "agents": _admin_agents,
    "tasks": _admin_tasks,
    "guard": _admin_guard,
    "settings": _admin_settings,
    "maintenance": _admin_maintenance,
    "memory": _admin_memory,
    "hermes": _admin_hermes,
    "events": _admin_events,
}


def main() -> None:
    args = _parser().parse_args()
    if args.command == "serve":
        import uvicorn
        uvicorn.run(
            "samvit.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return
    if args.command == "migrate":
        from dotenv import load_dotenv
        load_dotenv()
        if args.url:
            import os
            os.environ["DATABASE_URL"] = args.url
        from samvit import db as sdb
        asyncio.run(_run_migrate(sdb))
        return
    if args.command == "connect":
        raise SystemExit(_run_connect(args))
    if args.command == "register":
        raise SystemExit(asyncio.run(_register(args)))
    if args.command == "rotate-token":
        raise SystemExit(asyncio.run(_rotate_token(args)))
    if args.command == "doctor":
        raise SystemExit(asyncio.run(_doctor(args)))
    if args.command == "admin":
        handler = ADMIN_DISPATCH.get(args.admin_command)
        if handler:
            raise SystemExit(asyncio.run(handler(args)))
        print(f"Unknown admin command: {args.admin_command}", file=sys.stderr)
        raise SystemExit(1)


async def _run_migrate(sdb) -> None:
    print("Running Samvit migrations…")
    await sdb.init()
    try:
        await sdb.run_migrations()
        print("Migrations complete.")
    finally:
        await sdb.close()
