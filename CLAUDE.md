# Samvit — Claude Code Context

Read this before touching any file. Saves you re-reading the codebase.

## What it is

**Samvit** = self-hosted MCP coordination server. Any AI client (Claude Code, Codex, Cursor, Kiro, Antigravity, OpenCode) connects via MCP and gets shared memory, atomic task locking, code search, and doc sharing. Runs on `localhost:8765`. PostgreSQL + pgvector backend. No cloud, no API keys.

**Core problem:** Multiple AI agents on the same project re-learn the same things and duplicate work. Samvit is the shared brain that stops both.

**User mental model:** Engineers talk to their AI client naturally. The client auto-calls Samvit tools invisibly. Engineers never write Samvit API calls.

## Team

- **Darshan** (you) — builder, `darshan` handle, Claude Code
- **Sachin** — `sachin`, Claude
- **Rahul** — `rahul`, Antigravity
- **Rehma** — `rehma`, Codex

## File map (skip exploration)

```
samvit/
  cli.py          — all CLI commands (serve, register, connect, doctor, admin, ...)
  main.py         — FastAPI app + MCP server, auth middleware
  auth.py         — bearer token auth, agent registration
  db.py           — asyncpg pool, migrations, schema
  embeddings.py   — local BAAI/bge-small-en-v1.5 embeddings (no cloud)
  rag.py          — pgvector semantic search
  codegraph.py    — AST-based code index per repository
  guard.py        — blocks secrets/PII from being stored
  ratelimit.py    — per-agent sliding window
  tools/
    memory.py     — remember(), recall()
    tasks.py      — create_task(), claim(), done(), renew()
    messaging.py  — say(), read()
  integrations/
    hermes.py     — Hermes client integration
docs/
  USAGE.md        — full team onboarding guide
  DEPLOYMENT.md   — Docker, multi-machine, production
  ADR.md          — architecture decisions
website/
  index.html      — single-file marketing site (~950 lines)
migrations/       — SQL migration files
tests/            — pytest, asyncio_mode=auto
```

## Core APIs

| Tool | What it does |
|------|-------------|
| `remember(content, key?)` | Store decision/spec in shared memory |
| `recall(query or key)` | Retrieve by semantic search or exact key |
| `create_task(title)` | Create a task in the queue |
| `claim()` | Atomically lock next task (PostgreSQL CTE + FOR UPDATE SKIP LOCKED) |
| `done(task_id)` | Mark task complete |
| `index_code(path)` | Index repo by AST for semantic search |
| `search_code(query)` | Find code by meaning |
| `ingest(doc)` | Store a document |
| `search_docs(query)` | Search docs semantically |

## Key decisions (don't re-derive)

**Decorator deleted (v0.3.0 → removed):** We built a `@samvit.task` Python decorator. Deleted it. It confused the product identity — Samvit is not a Python library you import, it's an MCP server clients connect to. `samvit/decorators.py` and `tests/test_decorator.py` are gone. Don't re-add.

**No cloud, no external API calls:** Embeddings run locally via `BAAI/bge-small-en-v1.5`. Core design constraint.

**MCP over Streamable HTTP (primary), SSE (legacy):** `/mcp` for modern clients, `/legacy/sse` for older ones.

**PostgreSQL atomic task locking:** `claim()` uses CTE + `FOR UPDATE SKIP LOCKED`. No Redis, no Kafka for task state. This was a deliberate decision — see `docs/ADR.md`.

**gh-pages deployment:**
```bash
git push -f origin $(git subtree split --prefix website):gh-pages
```

**samvit connect:** Auto-writes MCP config for all known clients:
```bash
samvit connect --url http://HOST:8765 --token TOKEN
# or: --client claude,codex  or: --client all
```

## Stack

| Component | Tech |
|-----------|------|
| API + MCP | FastAPI + official MCP Python SDK |
| Storage | PostgreSQL 16 + pgvector |
| Embeddings | `BAAI/bge-small-en-v1.5` (local, 384-dim) |
| Admin UI | React + TypeScript + Vite (pre-built, served at `/admin`) |
| Rate limiting | Per-agent sliding window (in-process) |

## Running locally

```bash
docker compose up -d
pip install -e ".[dev]"
pytest -v

samvit serve --host 127.0.0.1 --port 8765
samvit doctor
```

## What NOT to do

- Don't add a `@task` decorator or any Python-importable API — that's not the product
- Don't add cloud dependencies or external embedding APIs
- Don't create new doc files — update existing ones in `docs/`
- Don't expose port 8765 to public internet in examples
- Don't `grep` or `find` the whole repo to understand structure — use this file
