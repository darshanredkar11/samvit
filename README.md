# Samvit

**Coordination server for multi-AI teams. Solves: shared memory, atomic tasks, code/doc graphs, no duplication.**

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP-purple)](https://modelcontextprotocol.io)

## The Problem

Multiple AI agents (Claude Code, Codex, Cursor, Antigravity) work on the same project:
- Agent A learns the auth system. Agent B re-learns it (wasted tokens).
- Agent A starts "implement auth" task. Agent B accidentally does it too (duplicate work).
- Agent A finds a bug pattern. Agent B searches the codebase again (wasted context).

## The Solution

Samvit is a **shared brain** that any AI client can connect to:

- **Shared Memory** — Agent A learns something. Agent B asks. Gets the answer instantly.
- **Atomic Tasks** — Task is claimed by one agent. Others see it's taken. No duplicates.
- **Code Graph** — Search your codebase by meaning. All agents use the same index.
- **Doc Sharing** — Store knowledge once. All agents can find it.

Clients (Claude Code, Codex, Cursor, Antigravity) auto-call these when users ask naturally:
```
User: "Remember the auth system uses JWT"
→ Client auto-calls: remember()

User: "Implement the auth endpoint"
→ Client auto-calls: create_task() + claim() + execute + done()

User: "Find where we validate passwords"
→ Client auto-calls: search_code()
```

## Quick Start

```bash
git clone https://github.com/darshanredkar11/samvit.git
cd samvit
docker compose up -d
curl http://127.0.0.1:8765/ready
```

Register agents:
```bash
samvit register alice --provider claude-code
samvit register bob --provider cursor
```

Connect your client:
```bash
claude mcp add --transport http samvit \
  http://localhost:8765/mcp \
  --header "Authorization: Bearer samvit_TOKEN"
```

## Core APIs

| Function | Purpose | Example |
|----------|---------|---------|
| `remember(content, key?)` | Store a decision/spec for reuse | `remember("JWT uses RS256", key="auth_spec")` |
| `recall(query or key)` | Retrieve a remembered decision | `recall("auth_spec")` |
| `create_task(title)` | Start a task | `create_task("Implement auth")` |
| `claim()` | Atomically lock next task | `claim()` → one agent gets it, others can't |
| `done(task_id)` | Mark task complete | `done(task_id)` |
| `index_code(path)` | Index codebase for search | `index_code("/workspace")` |
| `search_code(query)` | Find code by meaning | `search_code("password validation")` |
| `ingest(doc)` | Store a document | `ingest(architecture_guide)` |
| `search_docs(query)` | Find docs by meaning | `search_docs("auth flow")` |

## Two Machines, One Team

```text
Machine A: Claude Code ──┐
                        ├── MCP/HTTP ── Samvit ── PostgreSQL + pgvector
Machine B: Antigravity ──┘
```

Each teammate receives a separate token. Agents do not connect directly. They
call Samvit, which authenticates them and stores memory, task state, and messages
in the shared database.

## Architecture

| Component | Technology |
|---|---|
| API and MCP server | FastAPI + official MCP Python SDK |
| Current MCP transport | Streamable HTTP |
| Relational and vector storage | PostgreSQL 16 + pgvector |
| Local embeddings | `BAAI/bge-small-en-v1.5` (384-dim) |
| Rate limiting | Per-agent sliding-window |
| Admin UI | React + TypeScript + Vite |

**No hosted account or model API required.** Everything runs on your infrastructure.

## Deployment

See the **[deployment guide](docs/DEPLOYMENT.md)** for single-machine, multi-machine,
and production deployment instructions, including health checks, backups, and hardening.

## Configuration

Important environment variables are documented in [.env.example](.env.example).

- `DATABASE_URL`: PostgreSQL connection string
- `SAMVIT_ADMIN_SECRET`: emergency token-reset secret
- `SAMVIT_BIND_ADDRESS`: defaults to localhost
- `SAMVIT_GUARD_MODE`: `redact`, `block`, `warn`, or `off`
- `SAMVIT_WORKSPACE`: repository mounted read-only at `/workspace`
- `SAMVIT_CODE_ROOTS`: allowed server-side indexing roots
- `SAMVIT_CORS_ORIGINS`: allowed browser origins

## Admin Dashboard

Samvit ships with a web-based admin dashboard for managing agents, inspecting tasks,
viewing guard violations, and monitoring server health.

**Features:**
- Role-based access control (admin, operator, auditor)
- Agent management (register, suspend, rotate tokens)
- Task management (force-release, cancel)
- Guard violations viewer + stats
- Audit log of all admin actions
- Real-time system metrics

The admin UI is pre-built and served at `http://localhost:8765/admin`.
Authenticate with any agent token and the `SAMVIT_ADMIN_SECRET`.

## Development

```bash
docker compose up -d postgres redpanda
pip install -e ".[dev]"
pytest -v
```

Run without Docker after configuring dependencies:

```bash
samvit serve --host 127.0.0.1 --port 8765
samvit doctor
```

## Documentation

- **[Deployment Guide](docs/DEPLOYMENT.md)** — Single-machine, multi-machine, and production setup
- **[Usage Guide](docs/USAGE.md)** — Complete team onboarding + examples
- **[Architecture Decisions](docs/ADR.md)** — Why we built it this way
- **[Security Policy](SECURITY.md)** — Compliance, deployment hardening, best practices
- **[Contributing](CONTRIBUTING.md)** — How to contribute
- **[Changelog](CHANGELOG.md)** — Release history
- **[Build Summary](ENTERPRISE_MINIMAL_COMPLETE.md)** — v0.2.0 completion report

## What Works

✅ Shared memory (remember/recall with semantic search)  
✅ Atomic task queue (claim/done with no duplicates)  
✅ Code graphs (index & search by meaning)  
✅ Document sharing (ingest & semantic search)  
✅ Multi-workspace isolation (team A can't see team B)  
✅ Admin dashboard (task management, audit log)  
✅ Ethical guard (auto-blocks secrets/PII)  
✅ Self-hosted (Docker, no API keys, no cloud)  

## What's Next

- Task ordering/dependencies
- Memory retention policies  
- Advanced admin roles
- Agent capability registry

## License

Apache 2.0. See [LICENSE](LICENSE).
