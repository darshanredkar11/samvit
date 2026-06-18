# Samvit Admin UI — Sequence Diagrams

## 1. Admin Login & Authentication

**Ideal Flow**
```mermaid
sequenceDiagram
    actor Admin
    participant Dashboard as Web Dashboard
    participant FastAPI as FastAPI
    participant MW as Auth Middleware
    participant Auth as auth.py
    participant DB as PostgreSQL

    Admin->>Dashboard: Enter admin bearer token
    Dashboard->>Dashboard: store in sessionStorage
    Dashboard->>FastAPI: GET /v1/admin/status\nAuthorization: Bearer <admin_token>
    FastAPI->>MW: admin_auth_middleware()
    MW->>Auth: authenticate(token)
    Auth->>DB: SELECT * FROM agents\nWHERE token_hash_sha256=$1
    DB-->>Auth: agent row {id, handle, role, suspended_at}
    alt Token invalid
        Auth-->>MW: None
        MW-->>FastAPI: 401
        FastAPI-->>Dashboard: 401
        Dashboard->>Admin: Show "Invalid token"
    else Agent suspended
        MW-->>FastAPI: 403 "Agent suspended"
        FastAPI-->>Dashboard: 403
        Dashboard->>Admin: Show "Agent suspended"
    else Agent role not admin/operator/auditor
        MW-->>FastAPI: 403 "Insufficient role"
        FastAPI-->>Dashboard: 403
        Dashboard->>Admin: Show "Not authorized — admin area"
    else Authorized
        MW->>MW: set _current_agent\ncheck role
        MW->>FastAPI: call_next(request)
        FastAPI->>DB: SELECT system stats\n(agents count, task counts, etc.)
        DB-->>FastAPI: aggregated stats
        FastAPI-->>MW: JSON response
        MW-->>Dashboard: 200 {status, stats, health}
        Dashboard->>Admin: Show dashboard overview
    end
```

## 2. List Agents (Admin)

```mermaid
sequenceDiagram
    actor Admin
    participant Dashboard as Dashboard / CLI
    participant FastAPI as FastAPI
    participant AdminMod as admin.py
    participant DB as PostgreSQL

    Admin->>Dashboard: Navigate to Agents page
    Dashboard->>FastAPI: GET /v1/admin/agents[?role=&suspended=&limit=&offset=]
    FastAPI->>FastAPI: admin_auth → agent with admin/operator role
    FastAPI->>AdminMod: list_agents(agent, role_filter, suspended_filter, limit, offset)
    AdminMod->>DB: SELECT a.id, a.handle, a.provider, a.role,\n       a.suspended_at, a.created_at,\n       (SELECT COUNT(*) FROM tasks WHERE created_by=a.id) AS tasks_created,\n       (SELECT COUNT(*) FROM tasks WHERE claimed_by=a.id) AS tasks_claimed\nFROM agents a\n[WHERE role=$N] [WHERE suspended_at IS NULL|NOT NULL]\nORDER BY a.created_at DESC\nLIMIT $N OFFSET $N
    DB-->>AdminMod: rows[]
    AdminMod->>AdminMod: compute last_active from recent tasks/messages
    AdminMod-->>FastAPI: {agents: [{...}, ...], total: N}
    FastAPI-->>Dashboard: 200 {agents, total}
    Dashboard->>Admin: Render table
```

## 3. Register Agent (Admin)

```mermaid
sequenceDiagram
    actor Admin
    participant Dashboard as Dashboard / CLI
    participant FastAPI as FastAPI
    participant Auth as auth.py
    participant AdminMod as admin.py
    participant DB as PostgreSQL

    Admin->>Dashboard: Fill handle + provider + role form
    Dashboard->>FastAPI: POST /v1/admin/agents\n{handle, provider, role}
    FastAPI->>FastAPI: admin_auth → admin role
    FastAPI->>AdminMod: register_agent(admin_agent, handle, provider, role)
    AdminMod->>Auth: register_agent(handle, provider)
    Auth->>DB: INSERT INTO agents\n(handle, provider, token_hash, token_hash_sha256, role)
    alt Duplicate handle
        DB-->>Auth: UniqueViolationError
        AdminMod-->>FastAPI: 409
        FastAPI-->>Dashboard: 409 {error}
    else Success
        DB-->>Auth: RETURNING id
        Auth-->>AdminMod: {agent_id, token}
        AdminMod->>AdminMod: log to admin_audit_log\n{admin_handle, "register_agent", target_id=agent_id}
        AdminMod-->>FastAPI: {agent_id, handle, provider, role, token}
        FastAPI-->>Dashboard: 201 {agent_id, handle, provider, role, token}
        Dashboard->>Admin: Show success + the one-time token
    end
```

## 4. Suspend / Unsuspend Agent

```mermaid
sequenceDiagram
    actor Admin
    participant Dashboard as Dashboard / CLI
    participant FastAPI as FastAPI
    participant AdminMod as admin.py
    participant DB as PostgreSQL

    Admin->>Dashboard: Click "Suspend" on agent bob
    Dashboard->>FastAPI: POST /v1/admin/agents/bob/suspend
    FastAPI->>FastAPI: admin_auth → admin role
    FastAPI->>AdminMod: suspend_agent(admin_agent, "bob")
    AdminMod->>DB: SELECT id, handle FROM agents WHERE handle=$1
    alt Agent not found
        DB-->>AdminMod: None
        AdminMod-->>FastAPI: 404
    else Agent is admin
        AdminMod-->>FastAPI: 400 "Cannot suspend another admin"
    else Success
        DB-->>AdminMod: row
        AdminMod->>DB: UPDATE agents SET suspended_at=now() WHERE handle=$1
        AdminMod->>AdminMod: log to admin_audit_log
        AdminMod-->>FastAPI: {ok: True, suspended_at: isoformat}
        FastAPI-->>Dashboard: 200
        Dashboard->>Admin: Show "Suspended" badge

    Admin->>Dashboard: Click "Unsuspend"
    Dashboard->>FastAPI: POST /v1/admin/agents/bob/unsuspend
    FastAPI->>AdminMod: unsuspend_agent(admin_agent, "bob")
    AdminMod->>DB: UPDATE agents SET suspended_at=NULL WHERE handle=$1
    AdminMod->>AdminMod: log to admin_audit_log
    AdminMod-->>FastAPI: {ok: True}
    FastAPI-->>Dashboard: 200
    Dashboard->>Admin: Show active status
```

## 5. Change Agent Role

```mermaid
sequenceDiagram
    actor Admin
    participant Dashboard as Dashboard / CLI
    participant FastAPI as FastAPI
    participant AdminMod as admin.py
    participant DB as PostgreSQL

    Admin->>Dashboard: Select role "operator" for agent bob
    Dashboard->>FastAPI: POST /v1/admin/agents/bob/role\n{role: "operator"}
    FastAPI->>FastAPI: admin_auth → admin role
    FastAPI->>AdminMod: set_agent_role(admin_agent, "bob", "operator")
    AdminMod->>DB: SELECT handle FROM agents WHERE handle=$1
    alt Agent not found
        DB-->>AdminMod: None
        AdminMod-->>FastAPI: 404
    else Cannot demote self
        AdminMod-->>FastAPI: 400 "Cannot change your own role"
    else Success
        AdminMod->>DB: UPDATE agents SET role=$1 WHERE handle=$2 RETURNING role
        DB-->>AdminMod: role
        AdminMod->>AdminMod: log to admin_audit_log
        AdminMod-->>FastAPI: {ok: True, role: "operator"}
        FastAPI-->>Dashboard: 200
    end
```

## 6. Rotate Agent Token (Admin)

```mermaid
sequenceDiagram
    actor Admin
    participant Dashboard as Dashboard / CLI
    participant FastAPI as FastAPI
    participant Auth as auth.py
    participant AdminMod as admin.py
    participant DB as PostgreSQL

    Admin->>Dashboard: Click "Rotate Token" on agent bob
    Dashboard->>FastAPI: POST /v1/admin/agents/bob/rotate
    FastAPI->>FastAPI: admin_auth → admin/operator role
    FastAPI->>AdminMod: rotate_agent_token(admin_agent, "bob")
    AdminMod->>DB: SELECT id FROM agents WHERE handle=$1
    alt Agent not found
        DB-->>AdminMod: None
        AdminMod-->>FastAPI: 404
    else Success
        AdminMod->>Auth: rotate_token(agent_id)
        Auth->>DB: UPDATE agents SET token_hash=$1, token_hash_sha256=$2 WHERE id=$3
        DB-->>Auth: UPDATE 1
        Auth-->>AdminMod: new_token
        AdminMod->>AdminMod: log to admin_audit_log
        AdminMod-->>FastAPI: {token: new_token}
        FastAPI-->>Dashboard: 200
        Dashboard->>Admin: Show new token (one-time display)
    end
```

## 7. Admin Task Management

```mermaid
sequenceDiagram
    actor Admin
    participant CLI / Dashboard
    participant FastAPI as FastAPI
    participant AdminMod as admin.py
    participant DB as PostgreSQL

    Note over Admin,DB: List all tasks
    Admin->>CLI: samvit tasks list --status pending --pretty
    CLI->>FastAPI: GET /v1/admin/tasks?status=pending&limit=50
    FastAPI->>AdminMod: admin_list_tasks(agent, {status: "pending"})
    AdminMod->>DB: SELECT t.*, creator.handle AS created_by, claimer.handle AS claimed_by\nFROM tasks t\nLEFT JOIN agents creator ON creator.id=t.created_by\nLEFT JOIN agents claimer ON claimer.id=t.claimed_by\nWHERE t.status=$1\nORDER BY t.created_at DESC LIMIT $2
    DB-->>AdminMod: rows[]
    AdminMod-->>FastAPI: {tasks: [...], total: N}
    FastAPI-->>CLI: 200
    CLI->>Admin: Render table

    Note over Admin,DB: Force-release a claimed task
    Admin->>CLI: samvit tasks release <task-id>
    CLI->>FastAPI: POST /v1/admin/tasks/{id}/release
    FastAPI->>AdminMod: release_task(admin_agent, task_id)
    AdminMod->>DB: SELECT id, status, claimed_by FROM tasks WHERE id=$1 FOR UPDATE
    alt Not claimed
        AdminMod-->>FastAPI: 400 "Task is not claimed"
    else Success
        AdminMod->>DB: UPDATE tasks SET status='pending', claimed_by=NULL, claimed_at=NULL, claim_token=NULL WHERE id=$1
        AdminMod->>AdminMod: log to admin_audit_log
        AdminMod-->>FastAPI: {ok: True}
    end

    Note over Admin,DB: Release all stale claims
    Admin->>CLI: samvit tasks release-stale
    CLI->>FastAPI: POST /v1/admin/tasks/release-stale
    FastAPI->>AdminMod: release_stale_claims(admin_agent)
    AdminMod->>DB: UPDATE tasks SET status='pending', claimed_by=NULL, claimed_at=NULL, claim_token=NULL\nWHERE status='claimed' AND claimed_at + claim_timeout + INTERVAL '5 minutes' < now()
    DB-->>AdminMod: "UPDATE N"
    AdminMod->>AdminMod: log to admin_audit_log
    AdminMod-->>FastAPI: {released: N}
```

## 8. Guard Violations (Admin View)

```mermaid
sequenceDiagram
    actor Admin
    participant CLI / Dashboard
    participant FastAPI as FastAPI
    participant AdminMod as admin.py
    participant DB as PostgreSQL

    Admin->>Dashboard: Navigate to Guard Logs
    Dashboard->>FastAPI: GET /v1/admin/guard/violations?agent=bob&category=credential&since=24h&limit=50
    FastAPI->>FastAPI: admin_auth → admin/auditor role
    FastAPI->>AdminMod: list_guard_violations(agent, agent_filter, category, since, limit)
    AdminMod->>DB: SELECT gv.*, a.handle\nFROM guard_violations gv\nJOIN agents a ON a.id=gv.agent_id\n[WHERE a.handle=$N] [WHERE gv.category=$N] [WHERE gv.created_at > $N]\nORDER BY gv.created_at DESC LIMIT $N
    DB-->>AdminMod: rows[]
    AdminMod-->>FastAPI: {violations: [...], total: N}
    FastAPI-->>Dashboard: 200
    Dashboard->>Admin: Render filterable table

    Note over Admin,DB: Guard stats
    Admin->>CLI: samvit guard stats --pretty
    CLI->>FastAPI: GET /v1/admin/guard/stats
    FastAPI->>AdminMod: guard_stats(agent)
    AdminMod->>DB: SELECT gv.category, gv.pattern_name, COUNT(*) as cnt\nFROM guard_violations gv\nGROUP BY gv.category, gv.pattern_name\nORDER BY cnt DESC
    DB-->>AdminMod: rows[]
    AdminMod->>DB: SELECT a.handle, COUNT(*) as cnt\nFROM guard_violations gv JOIN agents a ON a.id=gv.agent_id\nGROUP BY a.handle ORDER BY cnt DESC LIMIT 10
    DB-->>AdminMod: top_agents[]
    AdminMod-->>FastAPI: {by_pattern: [...], by_agent: [...], total: N}
    FastAPI-->>CLI: 200
    CLI->>Admin: Render summary
```

## 9. System Settings (Guard Mode, Maintenance)

```mermaid
sequenceDiagram
    actor Admin
    participant CLI / Dashboard
    participant FastAPI as FastAPI
    participant AdminMod as admin.py
    participant DB as PostgreSQL

    Note over Admin,DB: Get current settings
    Admin->>CLI: samvit settings get --pretty
    CLI->>FastAPI: GET /v1/admin/settings
    FastAPI->>AdminMod: get_settings(agent)
    AdminMod->>DB: SELECT key, value FROM system_settings
    DB-->>AdminMod: rows[]
    AdminMod-->>FastAPI: {guard_mode: "redact", maintenance: False, rate_limit: 120, ...}
    FastAPI-->>CLI: 200
    CLI->>Admin: Render settings

    Note over Admin,DB: Update guard mode
    Admin->>CLI: samvit guard set-mode block
    CLI->>FastAPI: POST /v1/admin/settings\n{guard_mode: "block"}
    FastAPI->>AdminMod: update_settings(admin_agent, {guard_mode: "block"})
    AdminMod->>AdminMod: validate guard_mode ∈ {block, redact, warn, off}
    AdminMod->>DB: INSERT INTO system_settings (key, value, updated_at)\nVALUES ('guard_mode', '"block"', now())\nON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()
    AdminMod->>AdminMod: log to admin_audit_log
    AdminMod-->>FastAPI: {ok: True, guard_mode: "block"}
    FastAPI-->>CLI: 200

    Note over Admin,DB: Toggle maintenance mode
    Admin->>CLI: samvit settings maintenance on
    CLI->>FastAPI: POST /v1/admin/maintenance\n{enabled: true}
    FastAPI->>AdminMod: set_maintenance(admin_agent, True)
    AdminMod->>DB: INSERT INTO system_settings (key, value) VALUES ('maintenance', 'true') ON CONFLICT DO UPDATE SET value='true'
    AdminMod->>AdminMod: log to admin_audit_log
    AdminMod-->>FastAPI: {ok: True, maintenance: True}
    FastAPI-->>CLI: 200
```

## 10. Admin Audit Log

```mermaid
sequenceDiagram
    participant AdminMod as admin.py (any mutation)
    participant DB as PostgreSQL

    Note over AdminMod,DB: Every admin mutation logs here
    AdminMod->>DB: INSERT INTO admin_audit_log\n(admin_handle, action, target_type, target_id, details)\nVALUES ($1, $2, $3, $4, $5)
    DB-->>AdminMod: logged

    Note over AdminMod,DB: Examples:
    Note over AdminMod: register_agent → target_type="agent", details={handle, provider, role}
    Note over AdminMod: suspend_agent → target_type="agent", target_id=agent_id
    Note over AdminMod: release_task → target_type="task", target_id=task_id
    Note over AdminMod: update_settings → target_type="setting", details={key, old_value, new_value}
    Note over AdminMod: rotate_token → target_type="agent", target_id=agent_id
```

## 11. SSE Event Stream (Dashboard Live Updates)

```mermaid
sequenceDiagram
    participant Dashboard as Web Dashboard
    participant FastAPI as FastAPI
    participant Events as admin.py (SSE)
    participant DB as PostgreSQL / Event Bus

    Dashboard->>FastAPI: GET /v1/admin/events/stream\nAuthorization: Bearer <admin_token>
    FastAPI->>FastAPI: admin_auth → admin/auditor role
    FastAPI->>FastAPI: Response(headers={Content-Type: text/event-stream})
    Note over FastAPI,Dashboard: Connection held open

    alt New task created (via tools.tasks.create)
        Note over Events: admin.py listens to DB triggers or polls
        Events-->>FastAPI: event: task.created\ndata: {"task_id":"...","title":"...","created_by":"..."}
        FastAPI-->>Dashboard: SSE event
        Dashboard->>Dashboard: Update tasks counter + mini table
    end

    alt Task claimed
        Events-->>FastAPI: event: task.claimed\ndata: {"task_id":"...","claimed_by":"..."}
        FastAPI-->>Dashboard: SSE event
    end

    alt Task completed
        Events-->>FastAPI: event: task.completed\ndata: {"task_id":"...","status":"done","claimed_by":"..."}
        FastAPI-->>Dashboard: SSE event
    end

    alt Guard violation
        Events-->>FastAPI: event: guard.violation\ndata: {"agent":"...","pattern":"...","category":"..."}
        FastAPI-->>Dashboard: SSE event
    end

    loop Every 30s
        Events-->>FastAPI: event: heartbeat\ndata: {"timestamp":"..."}
        FastAPI-->>Dashboard: SSE event
    end

    Dashboard->>FastAPI: Connection closed (navigate away)
    FastAPI->>FastAPI: Cleanup, close connection
```

## 12. Hermes Integration Status (Admin)

```mermaid
sequenceDiagram
    actor Admin
    participant CLI / Dashboard
    participant FastAPI as FastAPI
    participant AdminMod as admin.py
    participant HermesBridge as HermesCronBridge

    Admin->>CLI: samvit hermes cron-sync
    CLI->>FastAPI: POST /v1/admin/hermes/cron-sync
    FastAPI->>AdminMod: trigger_cron_sync(admin_agent)
    AdminMod->>HermesBridge: HermesCronBridge().sync_to_samvit()
    HermesBridge->>HermesBridge: load_crons(), create tasks, cancel orphans
    HermesBridge-->>AdminMod: {created, skipped, cancelled, crons_found}
    AdminMod->>AdminMod: log to admin_audit_log
    AdminMod-->>FastAPI: {ok: True, ...stats}
    FastAPI-->>CLI: 200
    CLI->>Admin: Show sync result table
```

## 13. Memory Explorer (Admin KV)

```mermaid
sequenceDiagram
    actor Admin
    participant CLI / Dashboard
    participant FastAPI as FastAPI
    participant AdminMod as admin.py
    participant DB as PostgreSQL

    Admin->>CLI: samvit memory kv-list global --pretty
    CLI->>FastAPI: GET /v1/admin/memory/kv/global
    FastAPI->>AdminMod: list_kv_namespace(agent, "global")
    AdminMod->>DB: SELECT kv.key, kv.updated_at, a.handle\nFROM kv_memory kv JOIN agents a ON a.id=kv.agent_id\nWHERE kv.namespace=$1\nORDER BY kv.updated_at DESC
    DB-->>AdminMod: rows[]
    AdminMod-->>FastAPI: {keys: [{key, agent, updated_at}, ...]}
    FastAPI-->>CLI: 200
    CLI->>Admin: Render table

    Admin->>CLI: samvit memory kv-get global skill.python --pretty
    CLI->>FastAPI: GET /v1/admin/memory/kv/global/skill.python
    FastAPI->>AdminMod: get_kv_value(agent, "global", "skill.python")
    AdminMod->>DB: SELECT kv.*, a.handle FROM kv_memory kv JOIN agents a ON a.id=kv.agent_id\nWHERE kv.namespace=$1 AND kv.key=$2
    alt Found
        DB-->>AdminMod: row
        AdminMod-->>FastAPI: {key, value, agent, updated_at}
    else Not found
        AdminMod-->>FastAPI: 404
    end
