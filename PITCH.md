# Samvit — Pitch Deck

> European market edition. Emphasises GDPR/data sovereignty, open standards, technical depth.

---

## Slide 1 — Title

**Samvit**
*Shared brain for multi-AI engineering teams.*

Self-hosted coordination server for AI coding agents.
MCP · PostgreSQL · Apache 2.0

---

## Slide 2 — The Cost of Coordination Failure

**Every AI-first team hits this wall.**

| What happens | Cost |
|---|---|
| Agent A learns your auth system. Agent B re-learns it next session. | 40–60% of tokens are repeated context |
| Agent A starts "implement auth". Agent B starts the same task. | Duplicate work, diverging implementations |
| Agent A finds a bug pattern. Agent B searches the codebase again. | Re-investigation on every session boundary |

> A 4-engineer team running 4 AI agents wastes **≈ 30 hours/week** in repeated context and duplicate effort.
> That is €120k/year at European senior engineering rates — before token costs.

---

## Slide 3 — What Samvit Is

**One server. Every agent connects. No coordination overhead.**

```
                  ┌─────────────────────────────────┐
Claude Code ──┐   │  Samvit (self-hosted)            │
Codex CLI    ─┤   │                                  │
Cursor       ─┼── │  Shared memory  (remember/recall)│── PostgreSQL 16
Kiro         ─┤   │  Atomic tasks   (claim/done)     │   + pgvector
Antigravity  ─┘   │  Code graph     (index/search)   │
                  │  Doc search     (ingest/search)  │
                  │  Audit log      (every action)   │
                  └─────────────────────────────────┘
```

Agents call MCP tools automatically — engineers just talk to their client normally.

---

## Slide 4 — The Technical Architecture

**Built on boring technology, on purpose.**

| Layer | Choice | Why |
|---|---|---|
| Protocol | MCP (Streamable HTTP + legacy SSE) | Open standard — any compliant client connects |
| Storage | PostgreSQL 16 + pgvector | One system, no operational complexity |
| Atomic task locking | `FOR UPDATE SKIP LOCKED` CTE | Correct by construction — no Redis, no Kafka |
| Embeddings | `BAAI/bge-small-en-v1.5` (local) | No external API — data never leaves your infra |
| Auth | bcrypt-hashed bearer tokens | Stateless, auditable, per-agent |
| Code intelligence | Python AST + regex for JS/Go/Rust/Java | No language server required |
| Rate limiting | Per-agent sliding window (in-process) | Zero infrastructure dependency |

> **No external API calls. No cloud dependency. No data leaves your infrastructure.**

---

## Slide 5 — Atomic Task Locking (The Hard Part)

**Why `FOR UPDATE SKIP LOCKED` matters.**

Two agents cannot claim the same task. This is not a "try and check" pattern — it is guaranteed by the database.

```sql
-- claim() uses a CTE with SKIP LOCKED
WITH next AS (
  SELECT id FROM tasks
  WHERE status = 'pending'
    AND (tags && $tags OR $tags = '{}')
  ORDER BY priority DESC, created_at
  LIMIT 1
  FOR UPDATE SKIP LOCKED   -- ← only one agent wins, even under concurrent load
)
UPDATE tasks SET status = 'claimed', claimed_by = $agent_id, ...
FROM next WHERE tasks.id = next.id
RETURNING *;
```

PostgreSQL acquires a row-level lock. The losing agent skips the locked row and either claims the next task or gets `null`. No retries, no eventual consistency, no race condition.

> This is the same pattern used in production job queues at Shopify and GitHub. It works correctly at scale without Redis or Kafka.

---

## Slide 6 — Shared Memory Architecture

**Semantic search over team knowledge. All local.**

1. Agent calls `remember("JWT uses RS256 with 24h expiry", key="auth_spec")`
2. Samvit runs `BAAI/bge-small-en-v1.5` locally → 384-dim embedding
3. Stored in `pgvector` alongside exact-key index
4. Any agent calls `recall("auth spec")` → cosine similarity search returns the right memory
5. Agent calls `recall("auth_spec")` → exact-key lookup, O(1)

**Result:** Second agent gets the answer in milliseconds. No re-explanation. No token spend on context Samvit already knows.

---

## Slide 7 — Code Intelligence (AST + Vector)

**Codebase understanding shared across all agents.**

`index_code(path)` builds a graph:

- **Python**: native AST → functions, classes, imports, call edges
- **JS/TS, Go, Rust, Java**: regex-based symbol extraction
- Nodes and edges stored in PostgreSQL + pgvector

Then agents query:
- `search_code("password validation")` → semantic match on function summaries
- `explore_code("AuthService")` → symbol definition + context
- `who_calls("hash_password")` → call-site graph
- `graph_symbol("JWTDecoder")` → BFS traversal of callers and callees

One index, shared by every agent. No per-session re-reading of the codebase.

---

## Slide 8 — GDPR & Data Sovereignty

**Designed for European deployment from day one.**

| Requirement | How Samvit handles it |
|---|---|
| Data stays in your jurisdiction | Entirely self-hosted; no external API calls, no telemetry |
| No cloud processing of code or decisions | All embeddings run locally (`BAAI/bge-small-en-v1.5`) |
| PII must not leak into AI memory | Ethical Guard scans every write — credentials, PII, live data — configurable `redact / block / warn` |
| Audit trail for every action | Every admin action, every guard violation, timestamped and stored in PostgreSQL |
| Right to erasure | Agent data, memories, and tasks are in a single PostgreSQL instance — standard `DELETE` covers it |
| No vendor lock-in | Apache 2.0, MCP open protocol, no proprietary storage formats |

> GDPR Article 25 (Data Protection by Design): Samvit's default is `SAMVIT_GUARD_MODE=redact` — credentials are scrubbed before storage, not after.

---

## Slide 9 — Security Architecture

**Security is not a layer — it is the default.**

```
Agent request
    │
    ▼
Auth Middleware           ← bcrypt-hashed bearer token, per-agent
    │
    ▼
Rate Limiter              ← sliding window, per-agent, in-process
    │
    ▼
Ethical Guard             ← scan for credentials, PII, live data
    │                        configurable: redact | block | warn | off
    ▼
Tool Handler              ← workspace-scoped query (multi-team safe)
    │
    ▼
PostgreSQL                ← row-level workspace isolation
```

**Admin interface:**
- Role-based access: `admin`, `operator`, `auditor`
- Every mutation logged to `admin_audit_log`
- Suspension without deletion — token revoked, data preserved for audit

---

## Slide 10 — Operational Simplicity

**One Docker Compose file. No sidecars. No agents to manage.**

```bash
docker compose up -d        # start server + postgres
samvit register alice       # issue bearer token
samvit connect --url http://SERVER:8765 --token TOKEN
# → auto-configures Claude Code, Cursor, Codex — whichever is installed
```

- **Single binary surface** — one FastAPI process, one PostgreSQL instance
- **No per-developer install** — engineers only need an MCP client
- **Admin dashboard** at `/admin` — built into the same container
- **Health check** at `/ready` — integrates with Kubernetes liveness probes
- **Migrations** run automatically on startup (`samvit serve`)

> Ops overhead: near zero. The only thing to maintain is a PostgreSQL backup.

---

## Slide 11 — Open Source / Apache 2.0

**No licensing risk. No vendor dependency. No lock-in.**

- Apache 2.0 — compatible with commercial use, redistribution, modification
- All dependencies are open source (FastAPI, MCP SDK, asyncpg, pgvector, BAAI model)
- MCP is an open protocol (Anthropic open-sourced it) — Samvit does not depend on Anthropic's services
- Any compliant MCP client works — not tied to Claude, OpenAI, or any model provider
- Self-hosted → you own the data, the model, and the deployment

**For EU procurement:** Apache 2.0 satisfies most enterprise OSS policies. No export-control concerns. No SaaS dependency for air-gapped environments.

---

## Slide 12 — Who It's For

**The minimum viable user: any team with 2+ AI coding agents.**

| Segment | Pain | Samvit value |
|---|---|---|
| Software consultancies | Multiple clients, multiple AI tools — no shared context | Workspace isolation, per-client deployment |
| Enterprise R&D teams | GDPR, IP sensitivity, can't use SaaS tools | Self-hosted, no cloud, full audit trail |
| Fintech / Legaltech | Compliance-first, code must not leave jurisdiction | Data sovereignty by design |
| AI-native startups | Rapid iteration, multiple agents, no coordination overhead | Fast setup, zero per-machine install |
| Dev tooling companies | Need to offer multi-agent coordination to customers | Apache 2.0 — embed or white-label |

---

## Slide 13 — Traction

**What exists today (v0.3.0):**

- ✅ Complete MCP server: memory, tasks, messaging, code graph, doc search
- ✅ Admin dashboard (React + TypeScript)
- ✅ `samvit connect` — one-command client setup for 6 AI tools
- ✅ `samvit demo` — 30-second local demo, no account
- ✅ 176 passing tests (asyncio, full PostgreSQL, no mocks)
- ✅ GDPR-aligned ethical guard (redact/block/warn)
- ✅ Multi-workspace isolation
- ✅ Docker Compose deploy, Dockerfile with multi-stage build
- ✅ Full documentation: usage, deployment, architecture decisions

**What's next:**
- Task dependencies
- Memory retention policies
- Agent capability registry
- Kubernetes Helm chart
- Workspace-scoped admin roles

---

## Slide 14 — The Team

| Handle | Role | Tool |
|---|---|---|
| Darshan | Builder | Claude Code |
| Sachin | — | Claude |
| Rahul | — | Antigravity |
| Rehma | — | Codex |

*All contributors are also users — Samvit coordinates its own development.*

---

## Slide 15 — The Ask

**What we need / what we're offering:**

We are looking for:
- **Early enterprise pilots** — engineering teams with 2+ AI agents, GDPR constraints, or multi-agent coordination pain
- **Technical feedback** from teams with production multi-agent deployments
- **Partnership conversations** with AI tooling vendors (IDE makers, agent framework authors)

We are offering:
- Apache 2.0 — use it, embed it, fork it
- Priority support for pilot deployments
- Co-design of features driven by real production pain

**Contact:** [redkar.darshan11@gmail.com](mailto:redkar.darshan11@gmail.com)
**Repo:** https://github.com/darshanredkar11/samvit
**Demo:** `docker compose -f docker-compose.demo.yml up --build`

---

*Samvit means "shared understanding" in Sanskrit.*
*That is what we built.*
