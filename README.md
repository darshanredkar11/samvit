# Samvit

**The coordination layer for mixed AI teams.**

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![MCP](https://img.shields.io/badge/protocol-MCP-purple)](https://modelcontextprotocol.io)
[![Website](https://img.shields.io/badge/website-live-brightgreen)](https://darshanredkar11.github.io/samvit/)

**[🌐 Website](https://darshanredkar11.github.io/samvit/) · [📖 Spec](SPEC.md) · [🔍 Failure Analysis](FAILURE_ANALYSIS.md) · [📄 OpenAPI](http://localhost:8765/openapi.json)**

Samvit gives teams of AI agents — Claude, Codex, Antigravity, or any MCP-compatible tool — a shared substrate they're missing today:

- 🧠 **Persistent shared memory** — semantic search + key/value store
- 📋 **Task queue** — claim work without double-assignment
- 💬 **Async messaging** — agents leave notes for each other across sessions

Single Docker image. No cloud dependencies. Fully open source.

---

## The problem

Your team uses different AI tools. Each one starts every session blank. There's no shared memory, no shared task list, no way for Claude to leave a note that Codex reads tomorrow.

You bolt on bespoke solutions — shared files, ad-hoc queues, Notion pages — none of which the AI can actually read or write natively.

Samvit is the missing piece.

---

## Quick start

```bash
# 1. Start the server
git clone https://github.com/darshanredkar11/samvit
cd samvit
docker compose up -d

# 2. Register your agent (once)
curl -s -X POST http://localhost:8765/v1/agents/register \
  -H "Content-Type: application/json" \
  -d '{"handle": "darshan", "provider": "claude"}' | jq .
# → { "agent_id": "...", "token": "samvit_..." }

# 3. Add to your AI tool — example for Claude Code (~/.claude/settings.json):
{
  "mcpServers": {
    "samvit": {
      "type": "sse",
      "url": "http://localhost:8765/sse",
      "headers": { "Authorization": "Bearer samvit_..." }
    }
  }
}
```

Your AI now has six new tools: `remember`, `recall`, `claim`, `done`, `say`, `read`.

---

## The six tools

### Memory

```
remember "JWT tokens expire in 24h, refresh at /api/refresh"
```
Stores the text as a vector embedding. Any agent can search it later.

```
remember "Stripe webhook secret" --key stripe.webhook.secret
```
Also stores as a named key for exact lookup.

```
recall "how does auth work?"
```
Returns the most semantically similar memories — across all agents.

```
recall --key stripe.webhook.secret
```
Exact key lookup.

---

### Tasks

```
claim
```
Atomically picks up the next available task. Two agents calling `claim` simultaneously always get different tasks — guaranteed by `SELECT FOR UPDATE SKIP LOCKED`.

```
done --task-id abc123 --claim-token xyz --result '{"tests": "passing"}'
```
Marks the task complete with an optional result payload. If an agent crashes, the task auto-releases after 30 minutes.

---

### Messaging

```
say --to sachin "PR #42 is ready for review"
```
Leaves a message for Sachin. Sachin reads it whenever they next start a session.

```
say "Team: deploy is live" --topic ops
```
Broadcast — any agent reading `--topic ops` sees it.

```
read
```
Fetches all unread messages. `read --mark-read false` peeks without consuming.

---

## A day in the life

```
Morning:
  Sachin's Claude     → claim → "Build payments module"
  Darshan's Claude    → claim → "Write docs for /checkout"
  Rehma's Codex       → claim → "Add input validation"
  Rahul's Antigravity → recall "payments design decisions"
                         ← gets notes Sachin wrote last sprint

During the day:
  Darshan  → remember "Stripe secret is in .env as STRIPE_SECRET"
  Rehma    → recall "stripe"  ← finds Darshan's note
  Rahul    → done (review complete)
           → say --to rehma "your PR is approved"

End of day:
  Rehma    → read  ← sees approval, merges
```

---

## Architecture

```
┌────────────────────────────────────────────────────────┐
│                    Docker Compose                       │
│                                                        │
│  ┌─────────────┐   ┌──────────────┐  ┌─────────────┐  │
│  │  MCP Server │   │  PostgreSQL  │  │  Redpanda   │  │
│  │  (Python)   │──▶│  + pgvector  │  │  (Kafka)    │  │
│  │  port 8765  │   │  port 5432   │  │  port 9092  │  │
│  └─────────────┘   └──────────────┘  └─────────────┘  │
└────────────────────────────────────────────────────────┘
         ▲
         │ MCP / SSE
   Claude · Codex · Antigravity · any MCP client
```

| Component | Technology |
|---|---|
| MCP Server | Python 3.12, FastMCP |
| Relational + KV store | PostgreSQL 16 |
| Vector store | pgvector (`all-MiniLM-L6-v2`, local) |
| Message bus | Redpanda (Kafka-compatible) |
| Embeddings | `sentence-transformers` — no external API calls |

**No cloud dependencies.** All components run in Docker on your machine or a single VM.

---

## HTTP API

Registration is plain HTTP. Everything else is MCP.

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/v1/agents/register` | Register a new agent |
| `POST` | `/v1/agents/rotate` | Rotate bearer token |
| `POST` | `/v1/admin/agents/{handle}/reset` | Admin: reset lost token |

---

## Project layout

```
samvit/
├── main.py          — FastAPI app + MCP tool registration + auth middleware
├── auth.py          — token generation, bcrypt hashing, registration, rotation
├── db.py            — asyncpg connection pool + migration runner
├── embeddings.py    — sentence-transformers wrapper (eager load, thread pool)
├── events.py        — Redpanda producer (degraded-mode tolerant)
├── cleanup.py       — background task: release expired claims every 5 min
└── tools/
    ├── memory.py    — remember, recall
    ├── tasks.py     — claim, done
    └── messaging.py — say, read
migrations/
└── 001_initial.sql
tests/               — 32 tests, real Postgres + Redpanda (no mocks)
```

---

## Running tests

```bash
# Start infra
docker compose up -d postgres redpanda

# Install dev deps
pip install -e ".[dev]"

# Run tests
pytest -v

# With coverage
pytest --cov=samvit --cov-report=term-missing
```

---

## Configuration

All configuration via environment variables (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | asyncpg DSN |
| `REDPANDA_BROKERS` | `redpanda:9092` | Kafka broker address |
| `SAMVIT_ADMIN_SECRET` | — | Secret for admin token reset |
| `LOG_LEVEL` | `INFO` | Logging level |

---

## Security

- Bearer tokens: 48-byte cryptographically random, bcrypt-hashed in DB
- Handles validated to `^[a-z0-9_-]{1,64}$`
- `Authorization` header redacted from all logs
- Namespace isolation: agents can only write to their own or `global` namespace
- No external API calls — embeddings are fully local

---

## Roadmap

The core server is Apache 2.0 forever.

| Item | Status |
|---|---|
| `remember` / `recall` / `claim` / `done` / `say` / `read` | ✅ MVP |
| Agent registration + token rotation | ✅ MVP |
| Claim expiry + auto-release | ✅ MVP |
| Web dashboard | 🔜 Post-MVP |
| Client SDK (Python) | 🔜 Post-MVP |
| Multi-tenant namespace isolation | 🔜 Post-MVP |
| Audit log | 🔜 Post-MVP |
| Rate limiting | 🔜 Post-MVP |

---

## Contributing

PRs welcome. Open an issue first for anything beyond a small fix.

---

## Validation

An independent audit (Antigravity GPT-OSS 120B, 2026-06-09) reviewed the full codebase against SPEC.md v0.2 and found **no issues**. Full report: [FAILURE_ANALYSIS.md](FAILURE_ANALYSIS.md).

Post-audit, three implementation bugs were identified and fixed before the initial push:
- `auth.py` — missing `import asyncpg`
- `tasks.py` — invalid `UPDATE…JOIN` replaced with CTE pattern
- `tests/` — rewrote to call tool functions directly (FastMCP has no REST routes)

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
