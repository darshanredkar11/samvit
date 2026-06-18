# Samvit

**The shared coordination server for Claude, Codex, Antigravity, and mixed AI teams.**

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP-purple)](https://modelcontextprotocol.io)

Samvit lets AI coding tools on different machines share:

- Persistent semantic and key/value memory
- An atomic task queue with ownership and renewable leases
- Directed and broadcast messages across sessions
- Searchable documents and an optional code knowledge graph

Samvit does not replace Claude Code, Antigravity, LangGraph, or CrewAI. It gives
otherwise isolated agents one neutral place to coordinate.

## Start Here

For a plain-English team setup, including Claude Code on one machine and
Antigravity on another, read the **[complete usage guide](docs/USAGE.md)**.

## Quick Start

```bash
git clone https://github.com/darshanredkar11/samvit.git
cd samvit
cp .env.example .env
docker compose up -d
curl http://127.0.0.1:8765/ready
```

Register an agent:

```bash
docker compose exec samvit samvit register darshan \
  --provider claude-code \
  --url http://127.0.0.1:8765
```

Connect Claude Code:

```bash
claude mcp add --transport http samvit \
  http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer samvit_YOUR_TOKEN"
```

The current MCP endpoint is `/mcp`. Legacy SSE clients can use `/legacy/sse`.

## Core Tools

| Area | Tools | Purpose |
|---|---|---|
| Memory | `remember`, `recall` | Preserve and retrieve team decisions |
| Tasks | `create_task`, `list_tasks`, `claim`, `renew`, `done`, `cancel_task` | Coordinate work without duplicate assignment |
| Messaging | `say`, `read` | Leave persistent direct or topic messages |
| Documents | `ingest`, `search_docs` | Search shared documents by meaning |
| Code | `index_code`, `explore_code`, `who_calls`, `graph_symbol` | Query a server-mounted repository |

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

## Project Status

**v0.2.0** (Current) — Core coordination workflow is complete and production-ready:
- ✅ Atomic task queue with no double-assignment
- ✅ Shared memory (semantic + KV, namespaced)
- ✅ Messaging (direct + broadcast, topics)
- ✅ Code knowledge graph (Python/JS/TS parsing, semantic search)
- ✅ Admin dashboard with RBAC + audit logging
- ✅ Ethical guard (blocks secrets/PII automatically)
- ✅ Workspace isolation (multi-team safe)
- ✅ Self-hosted deployment (Docker, no dependencies)

**v0.3.0** (Planned):
- Task dependencies & retries
- Memory lifecycle & retention policies
- Workspace-scoped admin roles
- Agent capability registry

See [Specification](SPEC.md) for full feature roadmap.

## License

Apache 2.0. See [LICENSE](LICENSE).
