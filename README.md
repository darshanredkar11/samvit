# Samvit

**Self-hosted coordination server for multi-AI teams.**  
Shared memory, atomic task locking, code search, and document sharing — over MCP. No cloud. No API keys.

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP-purple)](https://modelcontextprotocol.io)

---

## The Problem

Multiple AI agents on the same project work in isolation. Agent A learns the auth system — Agent B re-learns it. Agent A starts the "implement auth" task — Agent B accidentally starts it too. Every agent searches the same codebase from scratch.

The result is wasted tokens, duplicate work, and diverging understanding.

## The Solution

Samvit is a shared brain that any AI client connects to. It runs on your machine, stores state in PostgreSQL, and exposes tools over MCP. Your clients (Claude Code, Codex, Cursor, Antigravity) auto-call these tools when you ask naturally — you never write Samvit API calls.

```
You: "Remember — auth uses JWT with RS256"
Your client auto-calls: remember("auth uses JWT with RS256", key="auth_spec")

You: "Implement the login endpoint"
Your client auto-calls: create_task() → claim() → execute → done()

You: "Where do we validate passwords?"
Your client auto-calls: search_code("password validation")
```

---

## Try the Demo (30 seconds)

No account. No API key. Runs entirely local.

```bash
git clone https://github.com/darshanredkar11/samvit.git
cd samvit
docker compose -f docker-compose.demo.yml up --build
```

Starts a pre-seeded workspace with 4 agents, 3 shared memories, and 5 tasks.  
The seed container prints ready-to-paste `claude mcp add` and `samvit connect` commands when done.

```
docker compose -f docker-compose.demo.yml down -v   # stop and wipe
```

---

## Quick Start

**1. Start the server**

```bash
git clone https://github.com/darshanredkar11/samvit.git
cd samvit
docker compose up -d
curl http://127.0.0.1:8765/ready
```

**2. Register agents**

```bash
samvit register alice --provider claude-code
samvit register bob --provider cursor
```

**3. Connect your AI clients**

```bash
# Auto-detect and configure all installed clients at once:
samvit connect --url http://localhost:8765 --token samvit_TOKEN

# Or connect a specific client:
samvit connect --url http://localhost:8765 --token samvit_TOKEN --client claude
```

Supports: Claude Code, Codex CLI, Cursor, Kiro, OpenCode, Antigravity.

**Manual (Claude Code):**

```bash
claude mcp add --transport http samvit \
  http://localhost:8765/mcp \
  --header "Authorization: Bearer samvit_TOKEN"
```

---

## Core API

| Tool | What it does |
|------|-------------|
| `remember(content, key?)` | Store a decision, spec, or fact for the whole team |
| `recall(query or key)` | Retrieve by semantic search or exact key |
| `create_task(title)` | Add a task to the shared queue |
| `claim()` | Atomically lock the next task — only one agent gets it |
| `done(task_id)` | Mark a task complete with optional structured result |
| `index_code(path)` | Build a semantic index of a codebase via AST |
| `search_code(query)` | Find code by meaning, not keyword |
| `ingest(doc)` | Store a document for team-wide search |
| `search_docs(query)` | Retrieve documents by semantic similarity |

---

## Architecture

```
Claude Code ──┐
Codex CLI    ─┤                     ┌─ shared memory
Cursor       ─┼── MCP/HTTP ── Samvit ─┤─ atomic task queue
Kiro         ─┤                     ├─ code graph (AST + pgvector)
Antigravity  ─┘                     └─ document store
                                         │
                                    PostgreSQL 16 + pgvector
```

Each agent authenticates with a bearer token. Agents don't talk to each other — they call Samvit, and Samvit keeps everything consistent.

| Component | Technology |
|-----------|------------|
| API + MCP server | FastAPI + official MCP Python SDK |
| MCP transport | Streamable HTTP (`/mcp`), legacy SSE (`/legacy/sse`) |
| Storage | PostgreSQL 16 + pgvector |
| Embeddings | `BAAI/bge-small-en-v1.5` — local, 384-dim, no cloud |
| Atomic task locking | `FOR UPDATE SKIP LOCKED` CTE — no Redis required |
| Rate limiting | Per-agent sliding window (in-process) |
| Admin UI | React + TypeScript + Vite, served at `/admin` |

**No hosted account or external API required.** Everything runs on your infrastructure.

---

## Deployment

See the **[Deployment Guide](docs/DEPLOYMENT.md)** for single-machine, multi-machine, Docker, and production hardening instructions.

Key environment variables (see also [`.env.example`](.env.example)):

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | — | PostgreSQL connection string |
| `SAMVIT_ADMIN_SECRET` | — | Emergency token-reset secret |
| `SAMVIT_BIND_ADDRESS` | `127.0.0.1` | Listen address |
| `SAMVIT_GUARD_MODE` | `redact` | Secret/PII guard: `redact` / `block` / `warn` / `off` |
| `SAMVIT_WORKSPACE` | `.` | Repository mounted at `/workspace` |
| `SAMVIT_CODE_ROOTS` | `/workspace` | Allowed server-side indexing roots |
| `SAMVIT_CORS_ORIGINS` | — | Allowed browser origins for admin UI |

---

## Admin Dashboard

Samvit ships a web-based admin dashboard at `http://localhost:8765/admin`.

- Agent management: register, suspend, rotate tokens
- Task management: inspect, force-release, cancel
- Guard violations: viewer and aggregate stats
- Audit log: every admin action, timestamped
- Role-based access: admin, operator, auditor

---

## Development

```bash
docker compose up -d postgres
pip install -e ".[dev]"
pytest -v
```

Run without Docker (after configuring dependencies):

```bash
samvit serve --host 127.0.0.1 --port 8765
samvit doctor
```

---

## Documentation

- **[Usage Guide](docs/USAGE.md)** — Complete team onboarding and workflow examples
- **[Deployment Guide](docs/DEPLOYMENT.md)** — Single-machine, multi-machine, production
- **[Architecture Decisions](docs/ADR.md)** — Why we built it this way
- **[Security Policy](SECURITY.md)** — Compliance, hardening, responsible disclosure
- **[Contributing](CONTRIBUTING.md)** — How to contribute
- **[Changelog](CHANGELOG.md)** — Release history

---

## Status

| Capability | Status |
|-----------|--------|
| Shared memory (remember / recall + semantic search) | ✅ |
| Atomic task queue (claim / done, no duplicates) | ✅ |
| Code graph (AST index + semantic search) | ✅ |
| Document sharing (ingest + semantic search) | ✅ |
| Multi-workspace isolation | ✅ |
| Admin dashboard | ✅ |
| Secret/PII guard | ✅ |
| Self-hosted Docker deploy | ✅ |

**Roadmap:** task dependencies, memory retention policies, agent capability registry.

---

## License

Apache 2.0. See [LICENSE](LICENSE).
