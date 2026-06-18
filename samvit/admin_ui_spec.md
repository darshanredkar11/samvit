# Samvit Admin Dashboard & CLI — AI Class Specification

## Overview

Samvit currently has zero admin UI — only raw API endpoints (`/health`, `/ready`, `/api/metrics`, `/v1/guard/violations`, `/v1/guard/status`). Admins and developers need a unified dashboard (web) and CLI to:

- See **real-time** agent activity, task flow, memory usage, messages
- Manage **agents** (register, rotate tokens, suspend)
- Inspect **guard violations** and audit logs
- Monitor **system health** (DB, Redpanda, embeddings)
- Control **system state** (pause task claiming, drain agents, maintenance mode)
- View **task queues** and manually intervene (force-release claims, cancel stuck tasks)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Samvit Server                            │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Admin API (/v1/admin/*)  ← new endpoints            │   │
│  └──────────────────────────────────────────────────────┘   │
│                         │ ▲                                 │
│                         ▼ │                                 │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Same DB pool — reads/writes the same tables         │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
         │ ▲                              │ ▲
         ▼ │                              ▼ │
┌─────────────────┐              ┌─────────────────┐
│  Web Dashboard  │              │  CLI (samvit)   │
│  (React SPA)    │              │  (Click/Go)     │
│  /admin/*       │              │  $ samvit ...   │
└─────────────────┘              └─────────────────┘
```

**No separate backend.** Dashboard and CLI talk directly to Samvit's HTTP API with an admin bearer token. Auth uses the SAME `Authorization: Bearer` header with a special `admin` scope role.

---

## Roles & Permissions

| Role | Scope | Can |
|---|---|---|
| `admin` | Full | Read/write all state, manage agents, system control |
| `auditor` | Read-only | View metrics, guard logs, task queues, agent list |
| `operator` | Tasks + agents | Force-release claims, cancel tasks, rotate tokens |

Stored as a new `role` column on the `agents` table:
```sql
ALTER TABLE agents ADD COLUMN role TEXT NOT NULL DEFAULT 'agent'
    CHECK (role IN ('agent', 'operator', 'auditor', 'admin'));
```

---

## Web Dashboard — Pages & Views

### 1. Login Page (`/admin/login`)

- Token input (bearer token for an `admin`/`auditor`/`operator` agent)
- Stored in `sessionStorage`, sent as `Authorization: Bearer` on every request
- On success: redirect to dashboard, show agent handle + role in header

### 2. Overview Dashboard (`/admin`)

**Top stats bar (live-updated every 5s via polling or SSE):**
```
┌──────┬──────┬──────┬──────┬──────┬──────┬────────┐
│ 12   │ 34   │ 5    │ 8    │ 156  │ 89   │ 2.3ms  │
│ Live │ Pend.│ Claim│ Done │ Mem. │ Msgs │ Redp.  │
│Agents│ Tasks│  ed  │/Fail │ories│      │ Latency│
└──────┴──────┴──────┴──────┴──────┴──────┴────────┘
```

- Agents: count of agents registered in last N minutes
- Pending/Claimed/Done+Failed: task status counts
- Memories/Messages: total stored
- Redpanda latency: last publish ms (from `events.status()`)

**Mini tables (last 10):**
- Recent tasks (id, title, status, claimed_by, age)
- Recent guard violations (pattern, agent, timestamp)
- Recent messages (from, to, topic, age)

**System status indicators:**
- Database: ✅ connected (pool: 3/10 busy)
- Redpanda: ⚠️ degraded (0 published, 2 failures)
- Embeddings: ✅ loaded (all-MiniLM-L6-v2)
- Cleanup loop: ✅ running (last run: 23s ago)
- Uptime: 12h 34m

### 3. Agents Page (`/admin/agents`)

**Table view:**
| Handle | Provider | Role | Created | Last Active | Tasks Created | Tasks Claimed | Status | Actions |
|---|---|---|---|---|---|---|---|---|
| alice | claude-code | admin | 2026-06-10 | 2m ago | 45 | 32 | ✅ active | [View] [Rotate] [Suspend] |
| bob | hermes | agent | 2026-06-11 | 15m ago | 12 | 28 | ✅ active | [View] [Rotate] [Suspend] |
| charlie | mcp-client | agent | 2026-06-12 | never | 0 | 0 | ⏸ suspended | [Unsuspend] [Delete] |

**Row detail panel (slide-out or modal):**
- Agent info: id, handle, provider, role, created_at
- Activity timeline: last 20 actions (task created/claimed/completed, messages sent)
- Task performance: avg completion time, success rate
- Guard violations for this agent (last 50)
- Token: [Rotate token] button

**Actions:**
- [Register agent] modal (handle, provider, role dropdown)
- [Suspend/Unsuspend] — sets `suspended_at` on agent; middleware rejects suspended agents
- [Rotate token] — triggers `auth.rotate_token()`
- [Change role] — dropdown (admin only)

### 4. Tasks Page (`/admin/tasks`)

**Filterable, sortable table:**
| ID | Title | Status | Priority | Tags | Worker | Creator | Claimer | Claimed | Age | Actions |
|---|---|---|---|---|---|---|---|---|---|---|
| uuid | Fix login bug | done | 3 | bug, frontend | any | alice | bob | 2h ago | 45m | ... |
| uuid | Deploy v2 | pending | 5 | deploy | deployer | alice | — | — | 10m | ... |
| uuid | Review PR | claimed | 2 | review | any | bob | charlie | 1m ago | 5m | ... |

**Actions per row:**
- [Force-release] — reset status to 'pending', clear claimed_by/claim_token (admin/operator)
- [Cancel] — set status to 'cancelled' (admin/operator)
- [View details] — full task data incl. result, timeline

**Bulk actions:**
- Release all expired claims
- Cancel all tasks matching filter

**Status bar chart:** distribution of tasks by status over last 24h.

### 5. Guard Logs Page (`/admin/guard`)

**Table:**
| Timestamp | Agent | Direction | Tool | Pattern | Category | Severity | Snippet |
|---|---|---|---|---|---|---|---|
| 2026-06-17 10:23 | alice | input | remember | aws_access_key | credential | high | AKIAJ... |
| 2026-06-17 10:22 | bob | output | recall | credit_card | pii | high | 4111... |

**Filters:**
- By agent (dropdown)
- By direction (input/output)
- By category (credential/pii/live_data/private_key)
- By severity
- Date range picker

**Summary cards:**
- Total violations today: 23
- Most blocked agent: alice (12 violations)
- Most common pattern: jwt_token (45% of violations)

### 6. Memory Explorer (`/admin/memory`)

- Text search across all KV memories (not just semantic)
- View/edit/delete any KV entry by namespace + key
- Show vector memory count, namespace distribution
- Purge entire namespace (careful: confirmation dialog)

### 7. Messages Page (`/admin/messages`)

- Searchable message history (both directed and broadcast)
- Filter by from_agent, to_agent, topic
- View message body, metadata, timestamps
- Purge old messages (date range)

### 8. System Settings (`/admin/settings`)

- Guard mode toggle (block ↔ redact ↔ warn ↔ off)
- Rate limit config (requests/window)
- View/copy env config (redacted secrets)
- Maintenance mode toggle (rejects all non-admin requests)
- Graceful shutdown button

### 9. Hermes Integration (`/admin/hermes`)

- View cron definitions loaded from config
- Last sync timestamp and result
- Skill watcher status (running/paused, last scan, published count)
- Force re-sync crons button
- Force re-publish all skills button

---

## CLI (`samvit`)

### Design

- Single binary (Go or Click/Python with `click`), stateless
- Reads `~/.samvit/config.json` for `{ "url": "...", "token": "..." }`
- All output is JSON by default (pipeable); `--pretty` flag for human-readable tables

### Commands

```
USAGE:
  samvit [--url <url>] [--token <token>] <command> [subcommands] [flags]

GLOBAL FLAGS:
  --url, -u     Samvit server URL (default: http://localhost:8765)
  --token, -t   Admin bearer token (or SAMVIT_ADMIN_TOKEN env var)
  --pretty, -p  Human-readable table output (default: JSON)
  --help, -h    Show help

COMMANDS:

  status                    System health overview
    --watch, -w             Live-updating dashboard (refresh every 2s)

  agents                    List all agents
    list                    Table: handle, provider, role, status, created, last_active
    get <handle>            Show agent details + activity
    register <handle>       Register a new agent (prompts for provider, role)
      --provider            Provider name
      --role                agent | operator | auditor | admin
    rotate <handle>         Rotate agent's token (outputs new token)
    suspend <handle>        Suspend an agent
    unsuspend <handle>      Re-activate a suspended agent
    set-role <handle>       Change agent role

  tasks                     Task queue management
    list                    List tasks (--status, --tags, --limit, --offset)
    get <task-id>           Show full task details + result
    release <task-id>       Force-release a claimed task back to pending
    cancel <task-id>        Cancel a pending task
    release-stale           Release all expired claims
    cancel-overdue          Cancel all overdue pending tasks

  guard                     Guard violation logs
    list                    List recent violations
      --agent               Filter by agent handle
      --category            Filter by category
      --direction           input | output
      --since               ISO timestamp or "1h", "24h" etc.
    stats                   Summary statistics (top agents, top patterns)
    set-mode <mode>         Change guard mode (block | redact | warn | off)

  memory                    Memory inspection
    kv-get <namespace> <key>    Get a KV memory
    kv-list <namespace>         List all KV keys in namespace
    kv-delete <namespace> <key> Delete a KV memory
    vector-stats                Vector memory totals by namespace

  messages                  Message inspection
    list                    List messages (--from, --to, --topic, --since)
    get <message-id>        Show full message

  hermes                    Hermes integration control
    cron-sync               Force sync crons from config
    cron-list               Show loaded cron definitions
    skill-publish           Force re-publish all skills
    skill-status            Show skill watcher status

  config                    Local CLI config
    set-token <token>       Save token to ~/.samvit/config.json
    set-url <url>           Save URL
    show                    Show current config (redacted token)

  events                    Event bus diagnostics
    status                  Show Redpanda connection status
    stats                   Published/failed counts, latency

  settings                  System settings
    get                     Show current settings
    set <key> <value>       Update a setting (guard mode, rate limit, etc.)
    maintenance <on|off>    Toggle maintenance mode

EXAMPLES:
  samvit status --pretty -w
  samvit agents list --pretty
  samvit tasks list --status pending --tags bug --pretty
  samvit guard set-mode block
  samvit memory kv-list global --pretty
```

---

## API Endpoints (New Admin Routes)

All mounted under `/v1/admin/` and bypass the normal agent auth (but still require a valid bearer token for an agent with `admin`/`operator`/`auditor` role):

```python
SKIP_AUTH_PATHS = {"/health", "/ready", "/api/metrics", "/v1/guard/status", "/v1/agents/register"}
# Admin paths ARE authenticated but checked for admin role
```

### New endpoints

```
# ── System ──────────────────────────────────────────────
GET    /v1/admin/status                    → full system health + stats
POST   /v1/admin/settings                  → update settings (body: {guard_mode, rate_limit, ...})
GET    /v1/admin/settings                  → current settings
POST   /v1/admin/maintenance               → toggle maintenance mode {enabled: bool}

# ── Agents ──────────────────────────────────────────────
GET    /v1/admin/agents                    → list all agents (paginated, filterable)
GET    /v1/admin/agents/{handle}           → agent detail + activity
POST   /v1/admin/agents                    → register agent {handle, provider, role}
POST   /v1/admin/agents/{handle}/rotate    → rotate token
POST   /v1/admin/agents/{handle}/suspend   → suspend
POST   /v1/admin/agents/{handle}/unsuspend → reactivate
POST   /v1/admin/agents/{handle}/role      → change role {role}

# ── Tasks (admin override) ─────────────────────────────
GET    /v1/admin/tasks                     → list all tasks (status, tags, pagination)
GET    /v1/admin/tasks/{id}                → full task detail
POST   /v1/admin/tasks/{id}/release        → force-release claim
POST   /v1/admin/tasks/{id}/cancel         → force-cancel
POST   /v1/admin/tasks/release-stale       → release all expired claims
POST   /v1/admin/tasks/cancel-overdue      → cancel overdue pending

# ── Guard ───────────────────────────────────────────────
GET    /v1/admin/guard/violations          → list (agent, category, direction, since)
GET    /v1/admin/guard/stats               → summary stats

# ── Memory (admin override) ────────────────────────────
GET    /v1/admin/memory/kv/{namespace}     → list KV entries in namespace
GET    /v1/admin/memory/kv/{namespace}/{key} → get specific KV
DELETE /v1/admin/memory/kv/{namespace}/{key} → delete KV
GET    /v1/admin/memory/vector/stats       → vector memory stats

# ── Messages ────────────────────────────────────────────
GET    /v1/admin/messages                  → list (from, to, topic, since, limit)
DELETE /v1/admin/messages/purge            → purge older than {before} date

# ── Hermes ──────────────────────────────────────────────
GET    /v1/admin/hermes/status             → cron bridge + skill watcher status
POST   /v1/admin/hermes/cron-sync          → trigger cron sync
GET    /v1/admin/hermes/crons              → loaded cron definitions
POST   /v1/admin/hermes/skill-publish      → force publish all skills

# ── Events ──────────────────────────────────────────────
GET    /v1/admin/events/status             → Redpanda connection + stats
```

### Middleware: Admin Role Check

```python
@app.middleware("http")
async def admin_auth_middleware(request: Request, call_next):
    if request.url.path.startswith("/v1/admin/"):
        agent = _current_agent.get()
        if not agent:
            return _error(401, "Not authenticated")
        if agent.get("role") not in ("admin", "operator", "auditor"):
            return _error(403, "Insufficient role — admin area")
        # Auditor is read-only
        if agent.get("role") == "auditor" and request.method not in ("GET",):
            return _error(403, "Auditor role is read-only")
    return await call_next(request)
```

---

## DB Changes

```sql
-- Agent role
ALTER TABLE agents ADD COLUMN role TEXT NOT NULL DEFAULT 'agent'
    CHECK (role IN ('agent', 'operator', 'auditor', 'admin'));

-- Agent suspension
ALTER TABLE agents ADD COLUMN suspended_at TIMESTAMPTZ;

-- System settings (KV table)
CREATE TABLE IF NOT EXISTS system_settings (
    key   TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Guard stats materialised? No — query guard_violations directly with COUNT/GROUP BY.

-- Index for admin queries
CREATE INDEX IF NOT EXISTS idx_agents_role ON agents(role);
CREATE INDEX IF NOT EXISTS idx_agents_handle ON agents(handle);
CREATE INDEX IF NOT EXISTS idx_guard_violations_agent ON guard_violations(agent_id, created_at DESC);
```

---

## UI Tech Stack (Recommendation)

| Layer | Choice |
|---|---|
| Framework | React 18 + Vite — SPA served by Samvit at `/admin/*` |
| UI Kit | shadcn/ui + Tailwind CSS (dark theme as primary) |
| Charts | Recharts (lightweight, composable) |
| Data fetching | TanStack Query (auto-refetch, cache, polling) |
| Real-time | Server-Sent Events (SSE) from `/v1/admin/events/stream` |
| HTTP client | fetch (built-in) or ky (lightweight wrapper) |
| Routing | React Router v6 |
| State | React context for auth; TanStack Query for server state |

### SSE Event Stream

```
GET /v1/admin/events/stream
→ text/event-stream

event: task.created
data: {"task_id": "...", "title": "...", "created_by": "..."}

event: task.claimed
data: {"task_id": "...", "claimed_by": "..."}

event: task.completed
data: {"task_id": "...", "status": "done", "claimed_by": "..."}

event: agent.registered
data: {"handle": "...", "provider": "..."}

event: guard.violation
data: {"agent": "...", "pattern": "...", "category": "..."}

event: heartbeat
data: {"timestamp": "..."}
```

Dashboard subscribes to this stream and updates UI in real-time without polling.

---

## CLI Tech Stack (Recommendation)

| Layer | Choice |
|---|---|
| Language | Go (single binary, cross-platform) or Python + Click + rich |
| HTTP | net/http (Go) or httpx (Python) |
| Tables | tablewriter (Go) or rich.table (Python) |
| Config | ~/.samvit/config.json (TOML or JSON) |
| Watch mode | periodic GET /v1/admin/status with tput clear |

---

## Screen Mockups (Text)

### Dashboard — Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ⚡ Samvit Admin                                  alice [admin]  ⏻ Live │
├──────────────────────────────────────────────────────────────────────────┤
│ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────────────┐ │
│ │ 12   │ │ 34   │ │ 5    │ │ 8    │ │ 156  │ │ 89   │ │ Healthy      │ │
│ │ Live │ │ Pend.│ │ Claim│ │ Done │ │ Mem. │ │ Msgs │ │ DB ✅ RP ⚠️   │ │
│ │Agents│ │Tasks │ │  ed  │ │/Fail │ │ories │ │      │ │ Embed ✅      │ │
│ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────────────┘ │
│                                                                          │
│  ├─ Recent Tasks ───────────────────────────────────────────────────────┤
│  │ ID       │ Title          │ Status  │ Agent  │ Age                  │
│  │ abc-123  │ Fix login bug  │ done    │ bob    │ 2m ago               │
│  │ def-456  │ Deploy v2      │ pending │ —      │ 10m ago              │
│  │ ghi-789  │ Review PR      │ claimed │ charlie│ 1m ago               │
│  └──────────────────────────────────────────────────────────────────────┘
│                                                                          │
│  ├─ Recent Guard Violations ────────────────────────────────────────────┤
│  │ Time         │ Agent    │ Pattern        │ Category                  │
│  │ 10:23:45     │ bob      │ aws_access_key │ credential                │
│  │ 10:22:12     │ alice    │ credit_card    │ pii                       │
│  └──────────────────────────────────────────────────────────────────────┘
└──────────────────────────────────────────────────────────────────────────┘
```

### CLI — `samvit tasks list --status pending --pretty`

```
┌───────────────────────────────────────────────────────────────────────────────┐
│  PENDING TASKS (12)                                                          │
├──────────────────────┬──────────────────┬──────────┬────────┬────────────────┤
│  ID                  │ TITLE            │ PRIORITY │ TAGS   │ AGE            │
├──────────────────────┼──────────────────┼──────────┼────────┼────────────────┤
│  abc-123             │ Fix login bug    │ 3        │ bug    │ 2h 15m         │
│  def-456             │ Deploy v2        │ 5        │ deploy │ 30m            │
│  ghi-789             │ Write tests      │ 1        │ test   │ 45m            │
└──────────────────────┴──────────────────┴──────────┴────────┴────────────────┘
```

---

## Implementation Phases

### Phase 1 — Admin API + DB migrations (backend only)
- Add `role` and `suspended_at` columns to `agents`
- Create `system_settings` table
- Implement all `/v1/admin/*` endpoints
- Admin auth middleware with role checking
- SSE event stream endpoint

### Phase 2 — CLI
- Implement `samvit` CLI with all commands
- Config file management
- Table output (--pretty)
- Watch mode (--watch)

### Phase 3 — Web Dashboard
- React SPA served from Samvit at `/admin/*`
- Login page with token input
- Overview dashboard with stats + mini tables
- Agents, Tasks, Guard pages
- SSE subscription for live updates

### Phase 4 — Advanced
- Memory explorer (KV + vector)
- Message search/purge
- Hermes integration management
- Settings UI (guard mode toggle, rate limit config)

---

## Security Considerations

- Dashboard served over HTTPS only (in production)
- CLI token stored in `~/.samvit/config.json` with `0600` permissions
- Admin bearer token is a **separate agent** with `admin` role — never use a regular agent token
- All admin actions logged to a new `admin_audit_log` table:
  ```sql
  CREATE TABLE admin_audit_log (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      admin_handle TEXT NOT NULL,
      action TEXT NOT NULL,
      target_type TEXT,   -- 'agent', 'task', 'setting', etc.
      target_id TEXT,
      details JSONB,
      created_at TIMESTAMPTZ DEFAULT now()
  );
  ```
- Suspended agents get `401` at the middleware level (before rate limiting)
- Auditor role can only GET — no mutations
