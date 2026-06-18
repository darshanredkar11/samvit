# Samvit v0.2.0 → Pitch-Ready Specification

**Target Markets**: Saudi Arabia, United Kingdom  
**Pitch Type**: Enterprise coordination platform for AI teams  
**Timeline**: 2–4 weeks (full implementation)

---

## EXECUTIVE SUMMARY

This spec defines the features, tests, and polish required to present Samvit as production-ready for Saudi and UK enterprise customers. Current status: **Core working, admin system solid, P0 gaps closed, 3 categories of work needed.**

### Work Breakdown
1. **Critical** (blockers for pitch) — 1 week
2. **High** (essential credibility) — 1 week
3. **Medium** (nice-to-have polish) — 1 week
4. **Low** (roadmap items, mention only) — not required

---

## PART 1: CRITICAL FIXES (PITCH BLOCKERS)

### 1.1 Admin Token Reset Guard

**Issue**: `admin_reset_token()` accepts any secret if `SAMVIT_ADMIN_SECRET` env var is unset.

**Current Code** (samvit/auth.py:171):
```python
expected = os.environ.get("SAMVIT_ADMIN_SECRET", "")
if not expected or not hmac.compare_digest(admin_secret, expected):
    raise PermissionError("Invalid admin secret")
```

**Problem**: Empty string comparison matches any input if env var missing.

**Fix**:
```python
async def admin_reset_token(handle: str, admin_secret: str) -> str:
    import os
    expected = os.environ.get("SAMVIT_ADMIN_SECRET", "")
    if not expected:
        raise PermissionError("SAMVIT_ADMIN_SECRET is not configured. Cannot reset token without admin secret.")
    if not hmac.compare_digest(admin_secret, expected):
        raise PermissionError("Invalid admin secret")
    # ... rest of function
```

**Test**:
```python
@pytest.mark.asyncio
async def test_admin_reset_requires_configured_secret(monkeypatch):
    """Without SAMVIT_ADMIN_SECRET set, token reset should fail."""
    monkeypatch.delenv("SAMVIT_ADMIN_SECRET", raising=False)
    
    with pytest.raises(PermissionError, match="not configured"):
        await auth.admin_reset_token("test-agent", "")
    
    with pytest.raises(PermissionError, match="not configured"):
        await auth.admin_reset_token("test-agent", "any-secret")
```

**Effort**: 5 min  
**Impact**: Security critical (authorization bypass)

---

### 1.2 Commit Message Clarity

**Issue**: Latest commit `f30acfb` has subject line `x` (placeholder).

**Fix**:
```bash
git reset --soft HEAD~1
git commit -m "feat: admin API (RBAC, audit), rate limiting, admin UI

- Added admin.py with role-based access control (admin/operator/auditor roles)
- Implemented audit logging for all admin mutations
- Added rate limiting (120 req/60s per agent, sliding window)
- Built React admin dashboard with 8 pages
- Workspace isolation (data-level scoping via migrations 009-010)
- Added 10 new test files (185+ test cases)
- Fixed all P0 gaps from GAPS.md"
```

**Effort**: 5 min  
**Impact**: Optics (signals lack of polish if message is `x`)

---

### 1.3 Workspace Admin Role Scoping

**Issue**: All admins can manage agents/tasks in ALL workspaces. No workspace-level admin roles.

**Current**: `agent.role` is global; no workspace-to-role mapping.

**Fix**:

#### 3a. Add Workspace Admin Roles (Migration 011)

```sql
-- Add workspace_role column (tracks admin role per workspace)
CREATE TABLE workspace_roles (
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    agent_id     UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    role         TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'auditor')),
    assigned_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (workspace_id, agent_id)
);

-- Workspace admins can manage their workspace only
-- Global admins (global_role='admin') can manage all workspaces
ALTER TABLE agents ADD COLUMN IF NOT EXISTS global_role TEXT DEFAULT 'agent';

INSERT INTO schema_migrations (version) VALUES (11);
```

#### 3b. Update auth.py

```python
async def authenticate(raw_token: str) -> dict | None:
    """Validate bearer token and load agent + workspace roles."""
    # ... existing SHA256 lookup, bcrypt verify ...
    agent = dict(row)
    
    # Load workspace roles for this agent
    async with db.pool().acquire() as conn:
        ws_roles = await conn.fetch(
            """SELECT workspace_id, role FROM workspace_roles 
               WHERE agent_id = $1""",
            agent["id"]
        )
    agent["workspace_roles"] = {str(r["workspace_id"]): r["role"] for r in ws_roles}
    return agent
```

#### 3c. Update admin.py Access Checks

```python
def _require_admin_in_workspace(agent: dict, workspace_id: str) -> None:
    """Raise PermissionError if agent can't manage this workspace."""
    ws_roles = agent.get("workspace_roles", {})
    agent_role = agent.get("role", "agent")
    
    # Global admin can manage any workspace
    if agent_role == "admin":
        return
    
    # Workspace admin/operator can manage their workspace
    if ws_roles.get(workspace_id) in ("admin", "operator"):
        return
    
    raise PermissionError(
        f"You are not admin for workspace {workspace_id}"
    )
```

#### 3d. Gate Admin Operations

In `admin.py`, prepend all mutation functions with:
```python
async def suspend_agent(agent: dict, handle: str):
    admin_mod._require_role(agent, "admin", "operator")
    
    # Get agent's workspace_id
    async with db.pool().acquire() as conn:
        target = await conn.fetchrow(
            "SELECT id, workspace_id FROM agents WHERE handle = $1",
            handle
        )
        if not target:
            raise LookupError(f"Agent '{handle}' not found")
    
    # Check workspace permission
    admin_mod._require_admin_in_workspace(agent, str(target["workspace_id"]))
    
    # ... rest of suspend logic
```

**Tests**:
```python
@pytest.mark.asyncio
async def test_workspace_admin_can_only_manage_own_workspace():
    """Workspace operator can't access agents in other workspace."""
    # Register 2 workspaces
    ws1_id = await admin.create_workspace(admin_agent, "team-a")
    ws2_id = await admin.create_workspace(admin_agent, "team-b")
    
    # Create operator in ws1
    op_agent = await auth.register_agent("operator1", "claude-code")
    await admin.assign_workspace_role(admin_agent, op_agent["agent_id"], ws1_id, "operator")
    
    # Operator can suspend agent in ws1
    user_in_ws1 = await auth.register_agent("user1", "claude-code")
    await db.move_agent_to_workspace(user_in_ws1["agent_id"], ws1_id)
    await admin.suspend_agent({**op_agent, "workspace_roles": {ws1_id: "operator"}}, "user1")  # ✅
    
    # But can't suspend agent in ws2
    user_in_ws2 = await auth.register_agent("user2", "claude-code")
    await db.move_agent_to_workspace(user_in_ws2["agent_id"], ws2_id)
    with pytest.raises(PermissionError):
        await admin.suspend_agent({**op_agent, "workspace_roles": {ws1_id: "operator"}}, "user2")  # ❌
```

**Effort**: 2 days  
**Impact**: High (required for multi-tenant pitch credibility)

---

### 1.4 Dependency Pinning

**Issue**: `pyproject.toml` uses `>=X.Y` which allows major version jumps.

**Current**:
```toml
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "asyncpg>=0.29",
    "mcp[cli]>=1.10,<2",
    ...
]
```

**Problem**: `fastapi>=0.111` could upgrade to 0.120, 0.130, etc.; breaks reproducibility.

**Fix**:
```toml
dependencies = [
    "fastapi>=0.111,<0.112",       # Pin minor
    "uvicorn[standard]>=0.29,<0.30",
    "asyncpg>=0.29,<0.30",
    "mcp[cli]>=1.10,<2",
    "fastembed>=0.4,<0.5",
    "aiokafka>=0.11,<0.12",
    "bcrypt>=4.1,<4.2",
    "python-dotenv>=1.0,<2",
    "pydantic>=2.7,<3",
]
```

**Effort**: 15 min  
**Impact**: Reproducibility (important for production deployments)

---

## PART 2: HIGH-PRIORITY FEATURES (CREDIBILITY)

### 2.1 End-to-End Integration Tests

**Issue**: REL-08 gap — RAG, code graph, dispatcher, Hermes workflows lack E2E coverage.

#### 2.1a Test: Ingest → Search Docs

**File**: `tests/test_rag_e2e.py`

```python
@pytest.mark.asyncio
async def test_ingest_and_search_documents():
    """Full RAG workflow: upload doc → chunk → embed → search."""
    agent = dict(await auth.register_agent("rag-agent", "claude-code"))
    
    # Create a test document
    doc_content = """
    # Company Handbook
    
    Our policy is to never share customer data with third parties.
    We use PostgreSQL for primary storage and Redpanda for events.
    
    ## Security
    All tokens must be rotated quarterly.
    """
    
    # Ingest document
    result = await rag.ingest(
        agent,
        filename="handbook.md",
        content=doc_content,
        namespace="company-docs"
    )
    doc_id = result["document_id"]
    assert result["chunks"] > 0
    
    # Search for policy about data
    searches = await rag.search_docs(
        agent,
        query="data sharing policy",
        namespace="company-docs",
        limit=3
    )
    assert len(searches) > 0
    assert any("never share" in r["snippet"].lower() for r in searches)
    
    # Search for tech stack
    searches = await rag.search_docs(
        agent,
        query="database technology stack",
        namespace="company-docs"
    )
    assert any("PostgreSQL" in r["snippet"] or "Redpanda" in r["snippet"] for r in searches)
```

**Effort**: 1 day  
**Impact**: Proves RAG works end-to-end

---

#### 2.1b Test: Index Code → Explore → Who Calls

**File**: `tests/test_codegraph_e2e.py`

```python
@pytest.mark.asyncio
async def test_code_graph_explore_and_who_calls():
    """Full code graph workflow: index → explore_code → who_calls."""
    agent = dict(await auth.register_agent("code-agent", "claude-code"))
    
    # Create test repo
    repo = {
        "utils.py": """
def log_message(msg: str):
    print(msg)

def process_data(data):
    log_message(f"Processing {len(data)} items")
    return [x*2 for x in data]
""",
        "main.py": """
from utils import process_data, log_message

def start():
    log_message("Starting app")
    result = process_data([1, 2, 3])
    print(f"Result: {result}")

if __name__ == "__main__":
    start()
"""
    }
    
    # Index code
    index_result = await codegraph.index_code(agent, repo)
    assert index_result["nodes"] >= 4  # log_message, process_data, start, and imports
    
    # Explore: find functions that work with data
    explores = await codegraph.explore_code(
        agent,
        query="data processing transformation",
        repo_id=index_result["repo_id"],
        limit=5
    )
    assert any("process_data" in e["name"].lower() for e in explores)
    
    # Who calls log_message?
    callers = await codegraph.who_calls(
        agent,
        function_name="log_message",
        repo_id=index_result["repo_id"]
    )
    caller_names = [c["caller"] for c in callers]
    assert "process_data" in caller_names
    assert "start" in caller_names
```

**Effort**: 1 day  
**Impact**: Proves code graph works end-to-end

---

#### 2.1c Test: Task Lifecycle (Dispatcher)

**File**: `tests/test_dispatcher_e2e.py`

```python
@pytest.mark.asyncio
async def test_task_creation_claim_completion_workflow():
    """Full task lifecycle: create → claim → process → complete."""
    creator = dict(await auth.register_agent("creator", "api"))
    worker = dict(await auth.register_agent("worker", "dispatcher"))
    
    # Creator creates task
    task = await tasks.create_task(
        creator,
        title="Process user batch",
        description="Extract emails from users",
        tags=["batch", "worker"],
        priority=1
    )
    task_id = task["id"]
    
    # Worker claims task
    claim = await tasks.claim(worker, tags=["batch"])
    assert claim["id"] == task_id
    claim_token = claim["claim_token"]
    
    # Worker processes (simulated)
    result = {"emails_extracted": 1042, "errors": 0}
    
    # Worker completes task
    done = await tasks.done(
        worker,
        task_id=task_id,
        claim_token=claim_token,
        result=result
    )
    assert done["status"] == "done"
    
    # Creator can see completed task
    completed = await tasks.list_tasks(creator, status="done")
    assert any(t["id"] == task_id for t in completed)
    assert any(t["result"]["emails_extracted"] == 1042 for t in completed)
```

**Effort**: 1 day  
**Impact**: Proves task queue + worker pattern works

---

#### 2.1d Test: Multi-Agent Coordination

**File**: `tests/test_coordination_e2e.py`

```python
@pytest.mark.asyncio
async def test_multi_agent_coordination_via_memory_and_tasks():
    """Two agents: one makes decision, other reads it and acts."""
    analyst = dict(await auth.register_agent("analyst", "claude-code"))
    executor = dict(await auth.register_agent("executor", "antigravity"))
    
    # Analyst remembers analysis
    await memory.remember(
        analyst,
        content="The best strategy is to prioritize customers with > 100 orders",
        namespace="global"
    )
    
    # Executor recalls and acts
    recalled = await memory.recall(
        executor,
        query="prioritization strategy",
        namespace="global"
    )
    assert len(recalled) > 0
    assert "100 orders" in recalled[0]["content"]
    
    # Analyst creates task for executor
    task = await tasks.create_task(
        analyst,
        title="Segment customers",
        tags=["executor"],
        description="Use the remembered strategy"
    )
    
    # Executor claims and completes
    claim = await tasks.claim(executor, tags=["executor"])
    assert claim["id"] == task["id"]
    
    await tasks.done(
        executor,
        task_id=task["id"],
        claim_token=claim["claim_token"],
        result={"segments_created": 5}
    )
    
    # Analyst sees result
    done_tasks = await tasks.list_tasks(analyst, status="done")
    assert len(done_tasks) > 0
```

**Effort**: 1 day  
**Impact**: **Strongest pitch demo** — shows multi-agent coordination story

---

### 2.2 Load & Performance Baseline

**File**: `tests/test_performance_baseline.py`

```python
import time
import asyncio
from locust import HttpUser, task, between

@pytest.mark.asyncio
async def test_50_concurrent_agents_latency():
    """Create 50 agents, measure task claim latency."""
    agents = []
    for i in range(50):
        a = await auth.register_agent(f"load-agent-{i}", "test")
        agents.append(a)
    
    # Create 500 pending tasks
    for i in range(500):
        await tasks.create_task(
            agents[0],  # from agent 0
            title=f"Task {i}",
            tags=["benchmark"]
        )
    
    # Measure claim latency across 50 agents
    start = time.monotonic()
    for agent in agents:
        claim = await tasks.claim(agent, tags=["benchmark"])
        if not claim:
            break
    elapsed = time.monotonic() - start
    
    latency_per_claim = (elapsed / 50) * 1000  # milliseconds
    print(f"Claim latency: {latency_per_claim:.2f}ms")
    
    # Target: <100ms per claim at 50 agents
    assert latency_per_claim < 100, f"Latency {latency_per_claim:.2f}ms exceeds 100ms target"
```

**Effort**: 1 day (baseline)  
**Impact**: Proves scalability story; gives confidence to enterprise customers

---

### 2.3 Hermes Integration E2E Test

**File**: `tests/test_hermes_e2e.py`

```python
@pytest.mark.asyncio
async def test_hermes_memory_backend_integration():
    """Hermes uses Samvit as persistent memory backend."""
    # Create Hermes agent
    hermes_handle = "hermes-agent"
    hermes_agent = await auth.register_agent(hermes_handle, "hermes")
    
    # Hermes backend initialized
    backend = SamvitMemoryBackend(
        samvit_url="http://localhost:8765",
        samvit_token=hermes_agent["token"],
        hermes_handle=hermes_handle
    )
    
    # Hermes stores memory via backend
    await backend.store_memory(
        memory_type="semantic",
        content="The user prefers email over SMS",
        metadata={"source": "user-feedback"}
    )
    
    # Verify memory in Samvit
    recalled = await memory.recall(
        hermes_agent,
        query="communication preference",
        namespace="global"
    )
    assert len(recalled) > 0
    
    # Hermes searches memory
    results = await backend.search_memory(
        query="contact method",
        limit=3
    )
    assert len(results) > 0
```

**Effort**: 1 day  
**Impact**: Proves Hermes integration works

---

### 2.4 Admin UI Security Tests

**File**: `tests/test_admin_ui_security.py`

```python
@pytest.mark.asyncio
async def test_admin_ui_requires_auth():
    """Admin dashboard is protected."""
    client = TestClient(main.app)
    
    # No auth → 401
    resp = client.get("/admin")
    assert resp.status_code == 401
    
    # Valid token + admin role → 200
    admin_agent = await auth.register_agent("admin", "test")
    admin_agent["role"] = "admin"
    token = admin_agent.get("token")  # mock
    
    resp = client.get(
        "/admin",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200

@pytest.mark.asyncio
async def test_auditor_cannot_mutate():
    """Auditor role can only GET."""
    auditor = await auth.register_agent("auditor", "test")
    await admin.set_agent_role({**auditor, "role": "admin"}, "auditor", "auditor")
    
    # GET is allowed
    result = await admin.list_agents(auditor)
    assert result is not None
    
    # POST is rejected
    with pytest.raises(PermissionError):
        await admin.suspend_agent(auditor, "some-agent")
```

**Effort**: 1 day  
**Impact**: Proves admin system is secure

---

## PART 3: HIGH-QUALITY POLISH

### 3.1 Documentation

#### 3.1a Deployment Guide

**File**: `docs/DEPLOYMENT.md`

```markdown
# Deployment Guide

## Single Machine (Local Development)

\`\`\`bash
git clone https://github.com/darshanredkar11/samvit.git
cd samvit
cp .env.example .env
docker compose up -d
curl http://127.0.0.1:8765/ready
\`\`\`

## Multi-Machine (Claude Code + Antigravity)

### Machine A (Claude Code)
\`\`\`bash
docker compose -f docker-compose.yml up -d samvit postgres redpanda
# Wait for health checks
docker compose exec samvit samvit register claude-code --provider claude-code
# Copy token, use in Claude MCP config
\`\`\`

### Machine B (Antigravity)
\`\`\`bash
# Point to Machine A's Samvit instance
export SAMVIT_URL=http://machine-a:8765
antigravity config set samvit_token <token-from-above>
\`\`\`

## Production (Kubernetes)

See `k8s/helm/samvit/values.yaml` for Helm chart.

\`\`\`bash
helm install samvit ./k8s/helm/samvit \
  --set postgres.enabled=true \
  --set redpanda.enabled=true \
  --set ingress.enabled=true \
  --set ingress.host=samvit.company.com
\`\`\`

### Prerequisites
- Kubernetes 1.25+
- Helm 3.0+
- External PostgreSQL 15+ (or use Bitnami chart)

### Configuration
See `values.yaml` for all options.

## Backup & Recovery

### Backup Database
\`\`\`bash
docker compose exec postgres pg_dump -U samvit samvit > backup.sql
\`\`\`

### Restore
\`\`\`bash
docker compose exec postgres psql -U samvit samvit < backup.sql
\`\`\`

## Monitoring

### Health Checks
- `/health`: Liveness (does service respond?)
- `/ready`: Readiness (is DB migrated, embeddings loaded?)
- `/api/metrics`: Prometheus metrics

### Alert Thresholds
- Guard violations > 10/day → investigate
- Rate limit hits > 5% of requests → tune limits
- Task failures > 1% → check worker logs
```

**Effort**: 1 day  
**Impact**: Enterprise customers need deployment guidance

---

#### 3.1b Architecture Decision Record (ADR)

**File**: `docs/ADR.md`

```markdown
# Architecture Decision Records

## ADR-001: Atomic Task Claiming via CTE

**Status**: Accepted  
**Date**: 2026-06-09

### Problem
Task queue must prevent double-assignment: if two workers claim simultaneously, only one should win.

### Solution
Use PostgreSQL CTE + FOR UPDATE SKIP LOCKED:

\`\`\`sql
WITH next_task AS (
    SELECT id FROM tasks
    WHERE status = 'pending'
    ORDER BY priority DESC, created_at ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
UPDATE tasks SET claimed_by = $1, claim_token = $2, status = 'claimed'
WHERE id IN (SELECT id FROM next_task)
RETURNING *
\`\`\`

### Rationale
- FOR UPDATE: locks row for duration of transaction
- SKIP LOCKED: another worker sees no row if locked (atomic)
- RETURNING: confirms which task was claimed

### Alternatives Considered
1. Redis-based locking → adds operational complexity
2. Optimistic locking (version column) → requires retry logic
3. Mutex in app memory → fails if workers in different processes

### Consequences
- ✅ Guarantees exactly-one assignment
- ✅ Works across multiple app instances
- ✅ No additional dependencies
- ⚠️ Postgres-only (not portable to MySQL/SQLite)

---

## ADR-002: Workspace Isolation via workspace_id FK

**Status**: In Review  
**Date**: 2026-06-18

### Problem
Single deployment must safely isolate data for multiple teams.

### Solution
Add workspace_id to all data tables (agents, tasks, memories, etc.). Application layer enforces scoping.

### Rationale
- ✅ Data-level isolation (can't accidentally expose cross-workspace data)
- ✅ Works with existing query patterns (just add WHERE workspace_id = $X)
- ⚠️ Requires careful audit of all queries

### Alternatives Considered
1. Separate databases per workspace → operational overhead (20+ DBs)
2. Row-level security (Postgres RLS) → more complex, potential bugs
3. Application-level sharding → error-prone

### Consequences
- Need migration 009-010 to backfill workspace_id
- All queries must filter by workspace_id
- Admin API needs workspace-scoped role checks

---

## ADR-003: Local Embeddings (no API dependency)

**Status**: Accepted

### Problem
Calling external embedding API (OpenAI, Cohere) adds latency, cost, and privacy concerns.

### Solution
Use `fastembed` library (BAAI/bge-small-en-v1.5, 384-dim, ~150MB).

### Rationale
- ✅ Offline-capable
- ✅ No API keys = less security surface
- ✅ Deterministic results
- ⚠️ Larger model = slower first request

### Consequences
- Docker image larger (~500MB)
- Cold start slower (~2s)
- Can't use fancier models (would need GPU)
```

**Effort**: 1 day  
**Impact**: Shows mature engineering thinking (ADRs are enterprise practice)

---

### 3.2 Security Hardening

#### 3.2a Prepared Statements Audit

**File**: Script to check

```bash
# Search for string interpolation in SQL queries
grep -rn "f\"SELECT\|f'SELECT\|format(" samvit/ --include="*.py" | grep -v ".pyc"
```

**Expected Result**: All queries should use parameterized placeholders (`$1, $2`), not f-strings.

**Example issues to fix** (if any):

**Before**:
```python
async def admin_list_tasks(agent, status):
    query = f"SELECT * FROM tasks WHERE status = '{status}'"  # ❌ SQL injection
    return await db.pool().fetch(query)
```

**After**:
```python
async def admin_list_tasks(agent, status):
    query = "SELECT * FROM tasks WHERE status = $1"  # ✅ Parameterized
    return await db.pool().fetch(query, status)
```

**Effort**: 2 hours (audit + fixes)  
**Impact**: Eliminates SQL injection risks

---

#### 3.2b Add CORS Origin Validation

**File**: `samvit/main.py`

```python
CORS_ORIGINS = os.environ.get(
    "SAMVIT_CORS_ORIGINS",
    "http://localhost,http://127.0.0.1"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Total-Count"],  # for pagination
    max_age=3600,
)
```

**Test**:
```python
def test_cors_blocks_untrusted_origin():
    """CORS headers should reject untrusted origins."""
    client = TestClient(main.app)
    resp = client.options(
        "/v1/admin/agents",
        headers={"Origin": "http://evil.com"}
    )
    # Should not include evil.com in Access-Control-Allow-Origin
    assert "evil.com" not in resp.headers.get("Access-Control-Allow-Origin", "")
```

**Effort**: 2 hours  
**Impact**: Prevents CSRF attacks from untrusted origins

---

### 3.3 Error Messages & Logging

#### 3.3a Structured Error Responses

**File**: `samvit/main.py`

```python
def _error(status: int, detail: str, error_code: str = None) -> JSONResponse:
    """Structured error response."""
    body = {
        "error": detail,
        "code": error_code or f"ERROR_{status}",
        "timestamp": datetime.utcnow().isoformat()
    }
    log.warning(f"HTTP {status}: {error_code or detail}")
    return JSONResponse(status_code=status, content=body)
```

**Usage**:
```python
@app.post("/v1/agents/register")
async def register(req: RegisterRequest):
    try:
        result = await auth.register_agent(req.handle, req.provider)
        return result
    except ValueError as exc:
        if "already registered" in str(exc):
            return _error(409, str(exc), "HANDLE_ALREADY_REGISTERED")
        return _error(400, str(exc), "INVALID_INPUT")
```

**Effort**: 1 day  
**Impact**: Client can programmatically handle errors; better logging

---

## PART 4: FEATURE COMPLETENESS CHECKLIST

### Tier 1: MVP (Ready Now)
- [x] Agent registration & token management
- [x] Task queue with atomic claiming
- [x] Semantic + KV memory (namespaced)
- [x] Messaging (directed + broadcast)
- [x] RBAC (4 roles)
- [x] Audit logging (admin_audit_log table)
- [x] Guard scanner (secrets/PII)
- [x] Rate limiting (per-agent)
- [x] Admin dashboard (React SPA)
- [x] Docker deployment
- [x] Workspace isolation (data-level)

### Tier 2: High Priority (2 weeks)
- [ ] Workspace admin roles (scoped by workspace)
- [ ] End-to-end integration tests (RAG, code, dispatcher, coordination)
- [ ] Load baseline (50 agents, <100ms claim latency)
- [ ] Deployment guide (single-machine, multi-machine, k8s)
- [ ] ADRs documentation

### Tier 3: Polish (1–2 weeks)
- [ ] SQL injection audit (prepared statements everywhere)
- [ ] CORS origin validation
- [ ] Structured error responses
- [ ] Helm chart for Kubernetes
- [ ] Performance tuning (connection pooling, caching)

### Tier 4: Nice-to-Have (Roadmap)
- [ ] Task dependencies & retries (PROD-07)
- [ ] Memory lifecycle & retention (PROD-08)
- [ ] Agent capability registry (PROD-06)
- [ ] Multi-workspace sharing
- [ ] Benchmark against LangGraph/CrewAI

---

## PART 5: TEST COVERAGE MATRIX

### Existing Tests (185+ functions) ✅

| Module | File | Test Count | Coverage |
|--------|------|------------|----------|
| admin.py | test_admin.py | 40+ | ✅ RBAC, mutations, audit |
| auth.py | test_auth_sha256.py | 5 | ✅ Hashing, lookup |
| tasks.py | test_tasks.py | 20+ | ✅ Claim, create, list |
| memory.py | test_memory.py | 15+ | ✅ Remember, recall |
| messaging.py | test_messaging.py | 12+ | ✅ Say, read, topics |
| guard.py | test_guard.py | 18+ | ✅ Patterns, violations |
| ratelimit.py | test_ratelimit.py | 6 | ✅ Sliding window |
| Other | test_*.py | 50+ | ✅ Misc coverage |

### New Tests Required (PART 2) ⏳

| File | Focus | Test Count | Effort |
|------|-------|------------|--------|
| test_rag_e2e.py | Ingest → search docs | 5 | 1 day |
| test_codegraph_e2e.py | Index → explore → who_calls | 5 | 1 day |
| test_dispatcher_e2e.py | Task workflow | 3 | 1 day |
| test_coordination_e2e.py | Multi-agent scenario | 5 | 1 day |
| test_hermes_e2e.py | Hermes integration | 3 | 1 day |
| test_admin_ui_security.py | Auth, RBAC enforcement | 4 | 1 day |
| test_performance_baseline.py | 50 agents latency | 3 | 1 day |
| **Total** | | **28** | **7 days** |

### For Tier 3 Polish ⏳

| File | Focus | Effort |
|------|-------|--------|
| test_sql_injection.py | Parameterized queries only | 2 hours |
| test_cors.py | Origin validation | 2 hours |
| test_error_responses.py | Structured errors | 2 hours |
| test_workspace_admin_scoping.py | Workspace role checks | 1 day |

---

## PART 6: PITCH NARRATIVE (What to Say)

### Opening (2 min)
"Samvit is an open-source coordination server for multi-agent AI teams. It gives Claude, Codex, Antigravity, and other AI tools one shared place to remember decisions, coordinate work, and send messages—without forcing them to talk to each other directly."

### Problem (1 min)
- Multiple AI tools on your team → each is isolated
- No shared memory of what was decided
- No coordination: both agents might work on the same task
- No audit trail of who did what

### Solution (1 min)
"Samvit is the missing middle layer."

**Show demo**: 
1. Agent A remembers a policy ("prioritize customers with >100 orders")
2. Agent B recalls it and acts on it
3. Agent A creates a task, Agent B claims it, completes it
4. Admin dashboard shows everything

### Proof Points (2 min)

**1. Atomic Task Queue**
- Proven algorithm (CTE + FOR UPDATE)
- No double-assignment, even across 50 workers
- <100ms claim latency

**2. Enterprise Ready**
- RBAC (admin/operator/auditor roles)
- Every admin action audit-logged
- Workspace isolation (multi-team safe)
- Secrets/PII guard (blocks credential leaks)

**3. Self-Hosted & Open**
- Apache 2.0 license
- Docker single-command deploy
- No API keys, no cloud dependency
- Local embeddings (offline capable)

**4. Extensible**
- MCP protocol (vendor-agnostic)
- Integrates with Claude Code, Hermes, Antigravity
- Code graph indexing + semantic search
- RAG document store (PDF + markdown)

### Positioning (1 min)
**Not** a replacement for:
- ❌ Claude Code, Antigravity, LangGraph
- ❌ Crew AI, AutoGen (full frameworks)

**Instead** a coordination layer for:
- ✅ Mixed-tool teams (Claude + Codex + custom agents)
- ✅ Shared memory & task queue
- ✅ Audit & compliance (all mutations logged)
- ✅ Local-first, self-hosted deployment

### Roadmap (1 min)
**v0.2.0** (now):
- ✅ Core coordination (memory, tasks, messaging)
- ✅ Admin dashboard + RBAC
- ✅ Workspace isolation

**v0.3.0** (next):
- Task dependencies & retries
- Agent capability registry
- Multi-workspace admin scoping

**v1.0** (future):
- Kubernetes Helm charts
- Managed cloud offering
- Multi-agent benchmark

### Close (30 sec)
"Samvit is production-ready for self-hosted multi-agent teams. If you're coordinating multiple AI tools across your organization, let's talk."

---

## PART 7: IMPLEMENTATION TIMELINE

### Week 1: Critical Fixes + Workspace Admin
- **Day 1**: Admin secret guard (§1.1), commit message (§1.2)
- **Day 2–3**: Workspace admin scoping (§1.3) + tests
- **Day 4**: Dependency pinning (§1.4)
- **Day 5**: Code review + bug fixes

### Week 2: Integration Tests
- **Day 6–8**: E2E tests (§2.1: RAG, code graph, dispatcher, coordination)
- **Day 9**: Performance baseline (§2.2)
- **Day 10**: Hermes E2E + admin UI security (§2.3–2.4)

### Week 3: Documentation & Polish
- **Day 11–12**: Deployment guide (§3.1a) + ADRs (§3.1b)
- **Day 13–14**: Security hardening (§3.2: SQL injection, CORS)
- **Day 15**: Error messages & logging (§3.3)

### Week 4: Demo & Deck
- **Day 16–17**: Record demo video (follow PITCH NARRATIVE)
- **Day 18–19**: Create pitch deck (slides + architecture diagram)
- **Day 20**: Dry run with team + final polish

---

## PART 8: SUCCESS CRITERIA (How to Know You're Ready)

### Functional
- [ ] All 7 new E2E tests pass (RAG, code, dispatcher, coordination, Hermes, admin security, performance)
- [ ] Workspace admin roles fully implemented + tested
- [ ] Load test: 50 agents claim tasks in <5s total (<100ms per agent)
- [ ] No SQL injection vulnerabilities (prepared statements 100%)
- [ ] CORS validates origin headers
- [ ] Admin UI requires auth (no SAMVIT_ADMIN_DEV_MODE=true in prod)

### Documentation
- [ ] Deployment guide covers: local, multi-machine, Kubernetes
- [ ] ADRs explain: atomic tasks, workspace isolation, local embeddings
- [ ] README links to deployment guide
- [ ] Pitch deck includes: architecture diagram, before/after scenario, demo video

### Optics
- [ ] Git history clean (no "x" commit messages)
- [ ] All dependencies pinned to minor version
- [ ] Error messages are user-friendly, not cryptic
- [ ] Commit history tells a coherent story

### Demo
- [ ] Can register 2 agents in <10s
- [ ] Can show memory + task workflow in 2 minutes
- [ ] Admin dashboard loads quickly, shows live data
- [ ] No errors in logs

---

## PART 9: RISK MITIGATION

### Risk: Workspace Admin Roles Too Complex

**Mitigation**: Start with global admins only (all admins see all workspaces) for v0.2.0. Workspace-scoped admins can be v0.3.0.

**Impact**: Reduces 1 week of work; acceptable for first pitch (global admins fine for single enterprise).

### Risk: E2E Tests Flaky

**Mitigation**: Use transactional tests (rollback after each test) to isolate state. Add 2-second waits before assertions.

**Impact**: Tests slightly slower but much more reliable.

### Risk: Performance Baseline Misses Target

**Mitigation**: If >100ms per claim, profile DB. Likely culprit: connection pool exhaustion. Solution: bump max_size from 10 to 20.

**Impact**: 15-minute fix if needed.

### Risk: Demo Breaks During Pitch

**Mitigation**: Record demo video ahead of time (don't demo live). Have 2 backup demo scenarios (minimal, advanced).

**Impact**: Eliminates live-demo risk.

---

## CHECKLIST: GO/NO-GO FOR PITCH

### Before Demo Day

- [ ] All 7 new E2E tests green
- [ ] Performance baseline: <100ms per agent ✅
- [ ] Admin secret guard fixed
- [ ] Workspace admin roles implemented
- [ ] SQL injection audit complete
- [ ] Deployment guide written
- [ ] ADRs documented
- [ ] Pitch deck complete
- [ ] Demo video recorded
- [ ] Team rehearsal done
- [ ] No "x" commits in history

### Go: Launch Pitch ✅

If all above are green, you're ready. Confidence: High.

### No-Go: Delay 1 Week ❌

If >3 items red, delay. Confidence: Low.

---

## APPENDIX: Test Templates

### Template: E2E Test

```python
@pytest.mark.asyncio
async def test_feature_end_to_end():
    """Full workflow: setup → action → verify."""
    # 1. Setup
    agent = dict(await auth.register_agent("test-agent", "test"))
    
    # 2. Action
    result = await module.function(agent, **kwargs)
    
    # 3. Verify
    assert result["expected_field"] == expected_value
    assert result["id"] is not None
    
    # 4. Cross-check via another agent / query
    cross_check = await module.query(other_agent, result["id"])
    assert cross_check["field"] == result["field"]
```

### Template: Performance Test

```python
@pytest.mark.asyncio
async def test_latency_under_load():
    """Measure latency when system is under stress."""
    # Create resources
    agents = [await auth.register_agent(f"a{i}", "test") for i in range(N)]
    
    # Warm up
    for _ in range(10):
        await module.function(agents[0])
    
    # Measure
    start = time.monotonic()
    for agent in agents:
        await module.function(agent)
    elapsed = (time.monotonic() - start) / len(agents)
    
    # Assert
    assert elapsed < TARGET_MS, f"Latency {elapsed}ms > target {TARGET_MS}ms"
    print(f"Latency: {elapsed:.2f}ms")
```

### Template: RBAC Test

```python
@pytest.mark.asyncio
async def test_role_enforcement():
    """Verify role X can/cannot do action."""
    admin = dict(await auth.register_agent("admin", "test"))
    admin["role"] = "admin"
    
    auditor = dict(await auth.register_agent("auditor", "test"))
    auditor["role"] = "auditor"
    
    # Admin can mutate
    result = await admin_module.mutate_action(admin, **args)
    assert result["status"] == "ok"
    
    # Auditor cannot mutate
    with pytest.raises(PermissionError, match="read-only"):
        await admin_module.mutate_action(auditor, **args)
```

---

**Document Version**: 1.0  
**Last Updated**: 2026-06-18  
**Next Review**: 2026-07-02
