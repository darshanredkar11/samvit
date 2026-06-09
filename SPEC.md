# Samvit — Specification v0.2

> Provider-agnostic multi-agent coordination layer: shared memory, task queue, and async messaging for teams of mixed AI tools.

*v0.2 — incorporates second-AI review feedback. Name changed from Relay to Samvit.*

---

## 0. Plain-English Guide — What Samvit Does For You

### The problem in one sentence
You and your teammates each use a different AI tool. They have no idea the others exist, no shared memory, no shared to-do list, no way to message each other. Every session starts blank.

### What Samvit adds

**Shared memory** — one agent learns something, every agent can recall it.
> Darshan's Claude discovers the auth token expiry rules → writes them to Samvit.
> Rahul's Antigravity is working on the same codebase a day later → recalls them instantly.

**Task queue** — a shared to-do list that hands out work without double-assigning.
> Sachin creates tasks: "Write tests", "Fix CORS", "Review PR #42".
> Any free agent calls `claim` and gets the next available item, locked to them.
> When done, calls `done` with the result. If an agent crashes, the task auto-releases after 30 minutes.

**Async messaging** — agents leave messages for each other across sessions.
> Rehma's Codex finishes a review: `say --to darshan "PR #42 approved, one nit on line 87"`.
> Darshan's Claude reads it at the start of the next session, even though both agents were never running at the same time.

---

### Setup in 5 steps

```bash
# 1. Start the server (one command, runs locally)
git clone https://github.com/samvitai/samvit && cd samvit
docker compose up -d

# 2. Register your agent — one time per person
curl -X POST http://localhost:8765/v1/agents/register \
  -d '{"handle": "darshan", "provider": "claude"}'
# ← save the token it returns: "samvit_abc123..."

# 3. Tell your AI tool where Samvit lives
# Claude Code: add to ~/.claude/settings.json
{
  "mcpServers": {
    "samvit": {
      "type": "sse",
      "url": "http://localhost:8765/sse",
      "headers": { "Authorization": "Bearer samvit_abc123..." }
    }
  }
}

# 4. Done — your AI now has: remember, recall, claim, done, say, read

# 5. Every teammate repeats steps 2–3 with their own handle + AI tool
```

No cloud account. No API keys. No configuration file beyond one token per person.

---

### What a typical day looks like

```
Morning:
  Sachin's Claude      → claim → "Build payments module"
  Darshan's Claude     → claim → "Write API docs for /checkout"
  Rehma's Codex        → claim → "Add input validation to /checkout"
  Rahul's Antigravity  → recall "payments design decisions"
                          ← gets notes Sachin wrote last sprint

During the day:
  Darshan   → remember "Stripe webhook secret is in .env as STRIPE_SECRET"
  Rehma     → recall "stripe"  ← finds Darshan's note immediately
  Rahul     → done (review task complete)
            → say --to rehma "your validation PR is approved"

End of day:
  Rehma     → read  ← sees Rahul's approval, merges the PR
```

---

## 1. Problem

Teams of AI agents (Claude, Codex, Antigravity, custom models) currently have no shared substrate. Each agent is stateless across sessions, unaware of what other agents are doing, and unable to hand off work reliably. Developers bolt on bespoke queues, ad-hoc shared files, or external services — all of which require cloud accounts and are not reproducible.

**Samvit** is a single Docker image that gives any group of agents:

- **Persistent shared memory** — semantic search + key/value store
- **Task queue** — claim/complete work items with ownership and timeouts
- **Async messaging** — send and read messages scoped to agents or topics

All exposed over the **Model Context Protocol (MCP)**, so any MCP-compatible client (Claude Code, Codex, custom tool) connects without code changes.

---

## 2. Name & Branding

| Property | Value |
|---|---|
| Name | **Samvit** |
| Origin | Sanskrit — *unified consciousness / collective awareness* |
| Binary | `samvit` |
| Docker image | `samvitai/samvit` |
| License | Apache 2.0 (core server) |
| Tagline | *The coordination layer for mixed AI teams* |

---

## 3. Architecture

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
         ▲                ▲
         │ MCP/SSE        │ (internal)
   ┌─────┴──────┐   ┌─────┴──────┐
   │ Claude     │   │ Codex      │   Antigravity, etc.
   │ (Darshan)  │   │ (Rehma)    │
   └────────────┘   └────────────┘
```

### Components

| Component | Technology | Role |
|---|---|---|
| MCP Server | Python 3.12, FastMCP | Exposes all tools over MCP/SSE |
| Relational store | PostgreSQL 16 | Tasks, messages, agent registry, KV memory |
| Vector store | pgvector extension | Semantic memory (embeddings) |
| Message bus | Redpanda (Kafka-compatible) | Durable pub/sub for `say`/`read` |
| Embeddings | `sentence-transformers` (local) | No external API calls required |

**No cloud dependencies.** Everything runs locally or on a single VM.

---

## 4. Data Model

### 4.1 Agents

```sql
CREATE TABLE agents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    handle      TEXT UNIQUE NOT NULL,   -- e.g. "darshan", "rehma"
    provider    TEXT NOT NULL,          -- "claude", "codex", "antigravity", etc.
    token_hash  TEXT NOT NULL,          -- bcrypt hash of bearer token
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

### 4.2 Memory

```sql
-- Key/value memory
CREATE TABLE kv_memory (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    UUID REFERENCES agents(id),
    namespace   TEXT NOT NULL DEFAULT 'global',
    key         TEXT NOT NULL,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (agent_id, namespace, key)
);

-- Covering index for fast key lookups (beyond unique constraint)
CREATE INDEX idx_kv_memory_lookup ON kv_memory (agent_id, namespace, key);

-- Semantic memory (vector)
-- Embedding dimension: 384 (all-MiniLM-L6-v2).
-- If the embedding model changes, a migration adding a new VECTOR column
-- and re-embedding existing rows is required. The model name is stored in
-- metadata to make this detectable.
CREATE TABLE semantic_memory (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id      UUID REFERENCES agents(id),
    namespace     TEXT NOT NULL DEFAULT 'global',
    content       TEXT NOT NULL,
    embedding     VECTOR(384),
    embedding_model TEXT NOT NULL DEFAULT 'all-MiniLM-L6-v2',
    metadata      JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON semantic_memory USING ivfflat (embedding vector_cosine_ops);
```

### 4.3 Tasks

```sql
-- 'cancelled' reserved for future use; do not remove from enum.
CREATE TYPE task_status AS ENUM ('pending', 'claimed', 'done', 'failed', 'cancelled');

CREATE TABLE tasks (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title          TEXT NOT NULL,
    description    TEXT,
    status         task_status DEFAULT 'pending',
    created_by     UUID REFERENCES agents(id),
    claimed_by     UUID REFERENCES agents(id),
    claim_token    TEXT,
    tags           TEXT[] DEFAULT '{}',
    priority       INT DEFAULT 0,
    deadline       TIMESTAMPTZ,
    claim_timeout  INTERVAL DEFAULT '30 minutes',  -- abandoned claim expiry
    claimed_at     TIMESTAMPTZ,
    done_at        TIMESTAMPTZ,
    result         JSONB,
    created_at     TIMESTAMPTZ DEFAULT now()
);
```

**Claim expiry:** A background task (runs every 5 minutes) resets any task where
`status = 'claimed' AND claimed_at + claim_timeout < now()` back to `pending`,
clearing `claimed_by`, `claimed_at`, and `claim_token`. This prevents permanently
lost work when an agent crashes mid-task.

### 4.4 Messages

```sql
CREATE TABLE messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_agent  UUID REFERENCES agents(id),
    to_agent    UUID REFERENCES agents(id),  -- NULL = broadcast
    topic       TEXT,
    body        TEXT NOT NULL,
    metadata    JSONB DEFAULT '{}',
    read_by     UUID[] DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

---

## 5. Error Envelope

All error responses (HTTP 4xx/5xx) use a consistent envelope:

```json
{
  "error": "human-readable message",
  "code": 400,
  "field": "optional — which param caused the error"
}
```

Common codes:

| Code | Meaning |
|---|---|
| 400 | Bad request / missing required field |
| 401 | Missing or invalid bearer token |
| 403 | Token does not own the resource (e.g. wrong claim_token) |
| 404 | Resource not found |
| 409 | Conflict (e.g. handle already registered) |
| 500 | Internal server error |

---

## 6. MCP Tool API

All tools are exposed as MCP tools. Each request must carry a bearer token
(`Authorization: Bearer <token>`) identifying the calling agent.

### 6.1 Memory — `remember`

Store a piece of information persistently.

```json
{
  "tool": "remember",
  "params": {
    "content": "string",          // required — the text to remember
    "key": "string",              // optional — if set, also upserts into KV store
    "namespace": "string",        // optional, default = caller's own namespace
    "metadata": {}                // optional JSON metadata tags
  }
}
```

**Returns:** `{ "id": "<uuid>", "stored": true }`

**Behaviour:**
- Always embeds `content` and writes to `semantic_memory`.
- If `key` is provided, performs `INSERT … ON CONFLICT DO UPDATE` into `kv_memory`.
- `namespace` defaults to the calling agent's handle (private). Use `"namespace": "global"` for team-shared memory.

---

### 6.2 Memory — `recall`

Retrieve relevant memories.

```json
{
  "tool": "recall",
  "params": {
    "query": "string",            // semantic search query (required unless key set)
    "key": "string",              // optional — exact KV lookup (bypasses vector search)
    "namespace": "string",        // optional, default = caller's own namespace
    "limit": 5,                   // optional, default 5
    "min_score": 0.7              // optional cosine similarity threshold
  }
}
```

**Returns:**
```json
{
  "results": [
    {
      "id": "uuid",
      "content": "...",
      "score": 0.91,
      "agent": "darshan",
      "namespace": "global",
      "created_at": "..."
    }
  ]
}
```

**Behaviour:**
- If `key` is set, returns the KV value directly (no vector search).
- Otherwise performs vector similarity search within `namespace`.
- `namespace` defaults to caller's own namespace. Pass `"namespace": "global"` for shared search.

---

### 6.3 Tasks — `claim`

Atomically claim the next available task.

```json
{
  "tool": "claim",
  "params": {
    "tags": ["string"],           // optional — OR filter: tasks matching any tag
    "task_id": "string"           // optional — claim a specific task by ID
  }
}
```

**Tag filtering:** `tags` is an OR filter — a task matches if it has *any* of the supplied tags.

**Returns:**
```json
{
  "task": {
    "id": "uuid",
    "title": "...",
    "description": "...",
    "claim_token": "...",
    "priority": 0,
    "tags": [],
    "deadline": null,
    "claim_timeout_minutes": 30,
    "created_by": "sachin"
  }
}
```

Returns `{ "task": null }` if no tasks are available.

**Behaviour:** Uses `SELECT … FOR UPDATE SKIP LOCKED` to prevent double-claims. Sets
`status = 'claimed'`, `claimed_by`, `claimed_at`, and a random `claim_token`. The token
must be supplied to `done`. Tasks are returned in priority DESC, created_at ASC order.

---

### 6.4 Tasks — `done`

Mark a claimed task as complete or failed.

```json
{
  "tool": "done",
  "params": {
    "task_id": "string",          // required
    "claim_token": "string",      // required — must match stored token
    "result": {},                 // optional — output JSON
    "status": "done"              // "done" or "failed"; default "done"
  }
}
```

**Returns:** `{ "ok": true }`

**Behaviour:** Validates `claim_token` (returns 403 on mismatch). Sets `status`, `done_at`, `result`.
Publishes a `task.completed` or `task.failed` event to Redpanda.

---

### 6.5 Messaging — `say`

Send a message to an agent or broadcast to a topic.

```json
{
  "tool": "say",
  "params": {
    "to": "string",               // agent handle; omit or null for broadcast
    "topic": "string",            // optional topic label (used for broadcast channels too)
    "body": "string",             // required
    "metadata": {}                // optional
  }
}
```

**Broadcast:** When `to` is null, the message is stored with `to_agent = NULL` and
published to Redpanda topic `messages.broadcast`. Any agent calling `read` without a
`from` filter will receive broadcast messages.

**Returns:** `{ "message_id": "uuid" }`

**Behaviour:** Inserts into `messages` table. Publishes to Redpanda topic
`messages.<to_handle>` (directed) or `messages.broadcast`. Redpanda topics are
auto-created on first publish (default config); no pre-declaration required.

---

### 6.6 Messaging — `read`

Read messages addressed to the calling agent (including broadcasts).

```json
{
  "tool": "read",
  "params": {
    "topic": "string",            // optional filter by topic label
    "from": "string",             // optional filter by sender handle
    "limit": 20,                  // optional, default 20
    "mark_read": true             // optional, default true; set false to peek
  }
}
```

**Returns:**
```json
{
  "messages": [
    {
      "id": "uuid",
      "from": "sachin",
      "body": "...",
      "topic": "...",
      "sent_at": "..."
    }
  ]
}
```

**Behaviour:** Returns messages where `to_agent = caller OR to_agent IS NULL`,
excluding messages already in `read_by` for the caller. When `mark_read: false`,
the `read_by` array is not updated — useful for inspecting without consuming.

---

## 7. Agent Identity & Auth

- Each agent registers once with a handle and provider. Registration returns a bearer token.
- Tokens are bcrypt-hashed in the DB; raw tokens are never stored.
- All MCP tool calls must include `Authorization: Bearer <token>`.
- No OAuth, no cloud SSO — simple token auth suitable for local/self-hosted.

**Registration:**

```
POST /v1/agents/register
Body: { "handle": "darshan", "provider": "claude" }
→ 201: { "agent_id": "uuid", "token": "samvit_<random64>" }
→ 409: { "error": "handle already registered", "code": 409 }
```

**Token rotation:**

```
POST /v1/agents/rotate
Headers: Authorization: Bearer <current_token>
→ 200: { "token": "samvit_<new_random64>" }
```

The old token is immediately invalidated on rotation.

---

## 8. Schema Migrations

Migrations live in `migrations/` as plain numbered SQL files (`001_initial.sql`,
`002_add_claim_timeout.sql`, …). Applied in order at server startup via a lightweight
runner in `db.py`. No external migration tool required for MVP; Alembic may be
introduced post-MVP for autogeneration.

Migration versioning is tracked in a `schema_migrations` table:

```sql
CREATE TABLE schema_migrations (
    version     INT PRIMARY KEY,
    applied_at  TIMESTAMPTZ DEFAULT now()
);
```

---

## 9. MVP Scope

The MVP ships exactly these six tools plus agent registration and claim expiry cleanup.

| Item | Status |
|---|---|
| `remember` | MVP |
| `recall` | MVP |
| `claim` | MVP |
| `done` | MVP |
| `say` | MVP |
| `read` | MVP |
| Agent register / rotate | MVP |
| Claim expiry background task | MVP |
| Schema migration runner | MVP |
| Client SDK (thin Python wrapper) | Post-MVP |
| Web dashboard | Post-MVP |
| Multi-namespace isolation / RBAC | Post-MVP |
| Task webhooks | Post-MVP |
| Replay / audit log | Post-MVP |
| Rate limiting | Post-MVP |

---

## 10. Project Structure

```
samvit/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── samvit/
│   ├── __init__.py
│   ├── main.py            # FastMCP server entrypoint
│   ├── auth.py            # token verification middleware
│   ├── db.py              # asyncpg pool + migration runner
│   ├── embeddings.py      # sentence-transformers wrapper
│   ├── cleanup.py         # background task: expire stale claims
│   ├── tools/
│   │   ├── memory.py      # remember, recall
│   │   ├── tasks.py       # claim, done
│   │   └── messaging.py   # say, read
│   └── events.py          # Redpanda producer/consumer
├── migrations/
│   ├── 001_initial.sql
│   └── 002_claim_timeout.sql
├── tests/
│   ├── conftest.py        # shared fixtures: test DB, test agent tokens
│   ├── test_memory.py     # remember + recall (KV and vector paths)
│   ├── test_tasks.py      # claim, done, expiry
│   └── test_messaging.py  # say, read, broadcast, peek
└── SPEC.md
```

**Test coverage targets (MVP):**
- Unit tests: each tool's happy path and main error paths (invalid token, wrong claim_token, not found)
- Integration tests: full round-trip via MCP against a real test Postgres + Redpanda (Docker)
- No mocking of the database — tests hit a real schema to catch migration issues early

---

## 11. Docker Compose

Credentials are read from environment variables; defaults shown are for local dev only.
In production, use Docker secrets or an `.env` file excluded from version control.

```yaml
version: "3.9"
services:
  samvit:
    build: .
    ports: ["8765:8765"]
    environment:
      DATABASE_URL: postgresql://${POSTGRES_USER:-samvit}:${POSTGRES_PASSWORD:-samvit}@postgres:5432/${POSTGRES_DB:-samvit}
      REDPANDA_BROKERS: redpanda:9092
    depends_on:
      postgres: { condition: service_healthy }
      redpanda: { condition: service_healthy }

  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-samvit}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-samvit}
      POSTGRES_DB: ${POSTGRES_DB:-samvit}
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-samvit}"]
      interval: 5s
      retries: 10

  redpanda:
    image: redpandadata/redpanda:latest
    command:
      - redpanda start
      - --smp 1
      - --memory 512M
      - --kafka-addr PLAINTEXT://0.0.0.0:9092
      - --advertise-kafka-addr PLAINTEXT://redpanda:9092
    healthcheck:
      test: ["CMD-SHELL", "rpk cluster health | grep -q healthy"]
      interval: 5s
      retries: 20

volumes:
  pgdata:
```

---

## 12. MCP Client Config

Add to any MCP client (e.g. Claude Code `~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "samvit": {
      "type": "sse",
      "url": "http://localhost:8765/sse",
      "headers": {
        "Authorization": "Bearer samvit_<your_token>"
      }
    }
  }
}
```

---

## 13. Non-Goals (v1)

- No agent orchestration logic (Samvit coordinates, it does not direct)
- No built-in LLM calls (agents bring their own model)
- No UI / web dashboard
- No hosted / SaaS tier in v1
- No fine-grained RBAC (token = full access for that agent)

---

## 14. Open Core Model (future)

Core server (this repo) is Apache 2.0 forever. Potential commercial layer:

- Managed cloud hosting (Samvit Cloud)
- Multi-tenant namespace isolation
- Audit log + compliance export
- Enterprise SSO / RBAC

---

## 15. First-User Scenario

| Agent | Handle | Provider |
|---|---|---|
| Darshan | `darshan` | Claude Sonnet |
| Sachin | `sachin` | Claude |
| Rahul | `rahul` | Antigravity |
| Rehma | `rehma` | Codex |

**Example flow:**

1. Sachin creates a task: `claim` picks up "Implement auth module"
2. Darshan `claim`s it, does the work, calls `done` with a result
3. Darshan `remember`s the implementation decisions
4. Rahul `recall`s "auth" — gets Darshan's notes back via vector search
5. Rehma `say`s to Darshan: "Auth tests are passing"
6. Darshan `read`s the message next session

---

*Spec version 0.2*
