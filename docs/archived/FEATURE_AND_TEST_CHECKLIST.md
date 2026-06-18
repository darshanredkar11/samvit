# Feature & Test Checklist for Pitch Readiness

**Print this, check boxes as you implement.**

---

## PART A: CRITICAL FIXES (Must Do Before Pitch)

### A.1 Security Fixes

- [ ] **Fix: Admin Secret Guard (auth.py:164–194)**
  - Current: Accepts any secret if env var unset
  - Fix: Add `if not expected: raise PermissionError("SAMVIT_ADMIN_SECRET not configured")`
  - Test: `test_admin_reset_requires_configured_secret()`
  - Effort: 5 min | Impact: Security critical

- [ ] **Test: Auth Edge Cases**
  - [ ] Empty secret when no env var set → 403
  - [ ] Wrong secret when env var set → 403
  - [ ] Correct secret when env var set → 200
  - Effort: 15 min | Impact: Guards against auth bypass

### A.2 Code Quality Fixes

- [ ] **Fix: Commit Message (git reset --soft HEAD~1)**
  - Current: `f30acfb x`
  - Fix: `feat: admin API (RBAC, audit), rate limiting, admin UI`
  - Effort: 5 min | Impact: Optics (shows polish)

- [ ] **Test: Git History Clean**
  - [ ] No placeholder commit messages
  - [ ] All commits follow semantic versioning (feat:, fix:, docs:, etc.)
  - Effort: 5 min | Impact: Professional appearance

### A.3 Version/Dependency Fixes

- [ ] **Fix: Dependency Pinning (pyproject.toml)**
  - Current: `fastapi>=0.111` (allows major jump)
  - Fix: `fastapi>=0.111,<0.112` (pin minor)
  - Apply to: fastapi, uvicorn, asyncpg, fastembed, aiokafka, bcrypt, pydantic
  - Effort: 15 min | Impact: Reproducibility

- [ ] **Test: Build Reproducibility**
  - [ ] `pip install -e .` produces same versions twice
  - [ ] Docker build produces same image hash on rebuild
  - Effort: 10 min | Impact: Enterprise deployments

---

## PART B: HIGH-PRIORITY FEATURES (Need Before Pitch)

### B.1 Workspace Admin Roles (Migration 011)

- [ ] **Feature: Add workspace_roles Table**
  - File: `migrations/011_workspace_roles.sql`
  - Tables:
    - [ ] `workspace_roles` (workspace_id, agent_id, role, assigned_at)
    - [ ] Add `global_role` column to agents table (nullable, used for global admins)
  - Effort: 2 hours | Impact: Multi-team data isolation

- [ ] **Feature: Load Workspace Roles in Auth (auth.py)**
  - [ ] Query workspace_roles in `authenticate()`
  - [ ] Return agent with `workspace_roles` dict
  - [ ] Docstring updated
  - Effort: 1 hour | Impact: Admin can check workspace access

- [ ] **Feature: Workspace-Scoped Admin Checks (admin.py)**
  - [ ] Add `_require_admin_in_workspace(agent, workspace_id)` function
  - [ ] Modify all mutation functions:
    - [ ] `suspend_agent(agent, handle)` → check workspace
    - [ ] `unsuspend_agent(agent, handle)` → check workspace
    - [ ] `set_agent_role(agent, handle, role)` → check workspace
    - [ ] `rotate_agent_token(agent, handle)` → check workspace
    - [ ] `release_task(agent, task_id)` → check workspace
    - [ ] `cancel_task(agent, task_id)` → check workspace
    - [ ] Any other mutation endpoints
  - Effort: 2 days | Impact: Prevents cross-workspace admin access

- [ ] **Feature: Workspace Admin Assignment (admin.py)**
  - [ ] `assign_workspace_role(admin, agent_id, workspace_id, role: str)`
  - [ ] `list_workspace_admins(agent, workspace_id)`
  - [ ] `unassign_workspace_role(admin, agent_id, workspace_id)`
  - Effort: 1 day | Impact: Admin can delegate workspace management

- [ ] **Test: Workspace RBAC (test_workspace_rbac.py)**
  - [ ] [ ] Workspace operator can manage own workspace's agents
  - [ ] [ ] Workspace operator cannot access other workspace's agents
  - [ ] [ ] Global admin can access all workspaces
  - [ ] [ ] Auditor role (read-only) enforced per workspace
  - [ ] [ ] Operator cannot suspend in workspace they don't admin
  - [ ] [ ] Audit log shows workspace context
  - Effort: 1 day | Impact: Proves RBAC works correctly

---

### B.2 Integration Tests (7 new test files)

#### B.2a: RAG End-to-End (test_rag_e2e.py)

- [ ] **Test: Ingest Markdown Document**
  - [ ] Create document with headings, paragraphs, code blocks
  - [ ] Call `rag.ingest(agent, filename, content, namespace)`
  - [ ] Assert chunks created (count > 0)
  - [ ] Assert embeddings stored
  - Effort: 2 hours | Impact: Proves chunking works

- [ ] **Test: Search Documents by Semantic Query**
  - [ ] Ingest document about company policy
  - [ ] Search for "data sharing policy"
  - [ ] Assert returns chunks with policy content
  - [ ] Assert rank order (best match first)
  - Effort: 2 hours | Impact: Proves semantic search works

- [ ] **Test: Search by Exact Key**
  - [ ] Ingest document with metadata tags
  - [ ] Search by exact key
  - [ ] Assert exact match found
  - Effort: 1 hour | Impact: KV search works

- [ ] **Test: Namespace Isolation**
  - [ ] Store doc in namespace A
  - [ ] Search from namespace B
  - [ ] Assert no cross-namespace leakage
  - Effort: 1 hour | Impact: Isolation works

- [ ] **Test: PDF Support**
  - [ ] Create test PDF (header, paragraphs, images)
  - [ ] Ingest PDF
  - [ ] Search PDF content
  - Assert text extracted correctly
  - Effort: 2 hours | Impact: Proves PDF parsing

**Total: test_rag_e2e.py — 1 day | Impact: Proves RAG pipeline**

---

#### B.2b: Code Graph End-to-End (test_codegraph_e2e.py)

- [ ] **Test: Index Python Code**
  - [ ] Create simple Python repo (utils.py, main.py)
  - [ ] Call `codegraph.index_code(agent, repo)`
  - [ ] Assert nodes created (functions, classes)
  - [ ] Assert edges created (calls, imports, inherits)
  - Effort: 2 hours | Impact: Proves indexing works

- [ ] **Test: Explore Code by Semantic Query**
  - [ ] Index code with docstrings
  - [ ] Call `codegraph.explore_code(agent, query, repo_id)`
  - [ ] Assert matching symbols returned
  - [ ] Assert ranked by relevance
  - Effort: 2 hours | Impact: Proves semantic search

- [ ] **Test: Who Calls (Function Callers)**
  - [ ] Index: `foo()` calls `bar()`, `baz()` calls `bar()`
  - [ ] Call `codegraph.who_calls(agent, "bar", repo_id)`
  - [ ] Assert returns ["foo", "baz"]
  - Effort: 1 hour | Impact: Proves call graph

- [ ] **Test: Graph Symbol (Dependency Tree)**
  - [ ] Index code with imports + calls
  - [ ] Call `codegraph.graph_symbol(agent, "main", repo_id, depth=2)`
  - [ ] Assert returns symbol + dependencies
  - Effort: 1 hour | Impact: Proves dependency extraction

- [ ] **Test: Multi-Language Support**
  - [ ] Index Python + JavaScript
  - [ ] Assert both indexed correctly
  - [ ] Assert explore works on both
  - Effort: 2 hours | Impact: Proves language coverage

**Total: test_codegraph_e2e.py — 1 day | Impact: Proves code graph pipeline**

---

#### B.2c: Task Dispatcher E2E (test_dispatcher_e2e.py)

- [ ] **Test: Create Task**
  - [ ] Agent A creates task with title, description, tags
  - [ ] Assert task stored with status='pending'
  - Effort: 1 hour

- [ ] **Test: Worker Claims Task**
  - [ ] Agent B claims task by tag
  - [ ] Assert task status='claimed', claimed_by=B
  - [ ] Assert claim_token returned
  - Effort: 1 hour

- [ ] **Test: Worker Completes Task**
  - [ ] Agent B marks task done with result
  - [ ] Assert task status='done', result stored
  - [ ] Assert claimed_at, done_at timestamps set
  - Effort: 1 hour

- [ ] **Test: Creator Sees Result**
  - [ ] Agent A lists completed tasks
  - [ ] Assert task shows with result
  - Effort: 1 hour

- [ ] **Test: Task Timeout (Renewal)**
  - [ ] Agent B claims, waits timeout, renews
  - [ ] Assert claim_timeout extended
  - Effort: 1 hour

**Total: test_dispatcher_e2e.py — 1 day | Impact: Proves task queue + worker pattern**

---

#### B.2d: Multi-Agent Coordination E2E (test_coordination_e2e.py)

- [ ] **Test: Memory → Task → Completion Workflow**
  - [ ] Agent A remembers decision ("prioritize >100 orders")
  - [ ] Agent B recalls decision
  - [ ] Agent A creates task for B
  - [ ] Agent B claims, completes, stores result
  - [ ] Agent A sees result
  - Effort: 2 hours | Impact: Proves full coordination loop

- [ ] **Test: Direct Message Communication**
  - [ ] Agent A says to Agent B
  - [ ] Agent B reads message
  - [ ] Assert metadata, timestamp preserved
  - Effort: 1 hour

- [ ] **Test: Broadcast Message**
  - [ ] Agent A broadcasts message
  - [ ] Agent B, C read broadcast
  - [ ] Assert same message for all
  - Effort: 1 hour

- [ ] **Test: Topic-Filtered Messages**
  - [ ] Agent A says on topic "planning"
  - [ ] Agent B reads topic "planning"
  - [ ] Assert only topic messages returned
  - Effort: 1 hour

**Total: test_coordination_e2e.py — 1 day | Impact: Proves multi-agent coordination story (best for pitch demo)**

---

#### B.2e: Hermes Integration E2E (test_hermes_e2e.py)

- [ ] **Test: Hermes Memory Backend — Store**
  - [ ] Create Hermes agent
  - [ ] Use SamvitMemoryBackend to store memory
  - [ ] Assert memory appears in Samvit
  - Effort: 2 hours

- [ ] **Test: Hermes Memory Backend — Search**
  - [ ] Store memory via Hermes backend
  - [ ] Search via backend
  - [ ] Assert result returned
  - Effort: 1 hour

- [ ] **Test: Hermes Cron Bridge**
  - [ ] Create Hermes cron config with task
  - [ ] Trigger HermesCronBridge.sync_to_samvit()
  - [ ] Assert task created in Samvit
  - Effort: 2 hours

**Total: test_hermes_e2e.py — 1 day | Impact: Proves Hermes integration**

---

#### B.2f: Admin UI Security (test_admin_ui_security.py)

- [ ] **Test: Admin Login Required**
  - [ ] GET /admin without token → 401
  - [ ] GET /admin with token → 200
  - Effort: 1 hour

- [ ] **Test: Admin RBAC Enforced**
  - [ ] Agent role=agent, POST /v1/admin/* → 403
  - [ ] Agent role=auditor, POST /v1/admin/agents/{handle}/suspend → 403
  - [ ] Agent role=operator, POST /v1/admin/agents/{handle}/suspend → 200
  - Effort: 1 hour

- [ ] **Test: Auditor Read-Only**
  - [ ] Auditor GET /v1/admin/agents → 200
  - [ ] Auditor POST /v1/admin/agents → 403
  - [ ] Auditor PUT /v1/admin/settings → 403
  - Effort: 1 hour

- [ ] **Test: No Token Exposure**
  - [ ] Admin API never returns plaintext token
  - [ ] Audit log never contains token hash
  - Effort: 1 hour

**Total: test_admin_ui_security.py — 1 day | Impact: Proves admin system is secure**

---

#### B.2g: Performance Baseline (test_performance_baseline.py)

- [ ] **Test: Task Claim Latency at 50 Agents**
  - [ ] Create 50 agents
  - [ ] Create 500 pending tasks
  - [ ] Measure claim latency per agent
  - [ ] Assert <100ms per claim
  - [ ] Print latency histogram
  - Effort: 1 day | Impact: Proves scalability for enterprise

- [ ] **Test: Memory Recall Latency**
  - [ ] Store 1000 memories
  - [ ] Recall 50 queries
  - [ ] Assert <500ms per recall
  - Effort: 1 day (optional)

- [ ] **Test: Code Search Latency**
  - [ ] Index 10K symbols
  - [ ] Search 50 queries
  - [ ] Assert <1s per search
  - Effort: 1 day (optional)

**Total: test_performance_baseline.py — 1 day (core), 2 days (with optional tests)**

**Key target: <100ms claim latency with 50 concurrent agents**

---

### B.3 Deployment & Documentation

- [ ] **Create: docs/DEPLOYMENT.md**
  - [ ] Single-machine setup (docker compose)
  - [ ] Multi-machine setup (Machine A + Machine B example)
  - [ ] Environment variables documented
  - [ ] Health check verification steps
  - [ ] Backup/restore procedures
  - [ ] Monitoring & alerting guidance
  - Effort: 1 day | Impact: Enterprises know how to deploy

- [ ] **Create: docs/ARCHITECTURE.md**
  - [ ] Component diagram (agents → Samvit → Postgres/Redpanda)
  - [ ] Data flow (remember → store → recall)
  - [ ] Task queue architecture (claim → work → done)
  - [ ] Security model (RBAC, workspace isolation)
  - Effort: 1 day | Impact: Shows architectural maturity

- [ ] **Update: README.md**
  - [ ] Link to DEPLOYMENT.md
  - [ ] Link to ARCHITECTURE.md
  - [ ] Update feature list with new tests
  - Effort: 2 hours

---

## PART C: NICE-TO-HAVE POLISH (Optional, but Recommended)

### C.1 Security Hardening

- [ ] **Audit: SQL Injection (2 hours)**
  - [ ] Search codebase for f-strings in SQL
  - [ ] Verify all queries use parameterized placeholders ($1, $2)
  - [ ] Fix any violations
  - Test: `test_sql_injection_prevention.py` (mock injection attempt)

- [ ] **Feature: CORS Origin Validation (2 hours)**
  - [ ] Read SAMVIT_CORS_ORIGINS from env
  - [ ] Add CORSMiddleware with allow_origins list
  - [ ] Test: Origin header validation

- [ ] **Feature: Structured Error Responses (1 day)**
  - [ ] Create `_error(status, detail, error_code)` helper
  - [ ] All endpoints return: `{"error": "...", "code": "ERROR_CODE", "timestamp": "ISO8601"}`
  - [ ] Test: Error responses are structured

### C.2 Documentation

- [ ] **Create: docs/ADR.md (Architecture Decision Records)**
  - [ ] ADR-001: Atomic Task Claiming (CTE + FOR UPDATE)
  - [ ] ADR-002: Workspace Isolation (workspace_id FKs)
  - [ ] ADR-003: Local Embeddings (no API keys)
  - Effort: 1 day | Impact: Shows design maturity

- [ ] **Create: Kubernetes Helm Chart (optional, but impressive)**
  - [ ] `k8s/helm/samvit/Chart.yaml`
  - [ ] `k8s/helm/samvit/values.yaml`
  - [ ] Includes: samvit deployment, postgres, redpanda, ingress
  - Effort: 2 days | Impact: Enterprise deployments

### C.3 Demo Preparation

- [ ] **Record: Demo Video (15–20 min, scripted)**
  - Script:
    1. Register 2 agents (2 min)
    2. Show memory workflow: Agent A remembers, Agent B recalls (3 min)
    3. Show task workflow: A creates, B claims, completes (3 min)
    4. Show admin dashboard: Audit log, RBAC (3 min)
    5. Show performance: 50 agents, latency <100ms (2 min)
    6. Show guard: Try to remember secret, gets blocked (2 min)
  - Effort: 2 days (including retakes, editing)

- [ ] **Create: Pitch Deck (20–30 slides)**
  - Problem statement
  - Solution overview
  - Demo walkthrough (screenshot + narrative)
  - Architecture diagram
  - Feature matrix (vs LangGraph, CrewAI)
  - Security & compliance
  - Roadmap
  - Use cases
  - Pricing/commercial model
  - Q&A

---

## PART D: VALIDATION CHECKLIST (Before Go-Live)

### D.1 Functional Tests
- [ ] All 7 new E2E tests passing (green CI)
- [ ] Existing 185+ unit tests passing
- [ ] No SQL injection vulnerabilities
- [ ] No XSS vulnerabilities in admin UI
- [ ] CORS validation working
- [ ] Rate limiting enforced
- [ ] Admin auth required on all /v1/admin/* endpoints

### D.2 Performance
- [ ] Task claim latency <100ms at 50 agents ✅
- [ ] Memory recall latency <500ms ✅
- [ ] Code search latency <1s ✅
- [ ] No memory leaks (rate limiter cleanup running)

### D.3 Security
- [ ] Admin secret guard prevents unauth reset ✅
- [ ] Workspace roles enforce data isolation ✅
- [ ] Audit log records all mutations ✅
- [ ] Token hashing uses bcrypt + SHA256 ✅
- [ ] No plaintext tokens in logs ✅
- [ ] Guard blocks secrets/PII before storage ✅

### D.4 Documentation
- [ ] README updated with new features ✅
- [ ] DEPLOYMENT.md covers all scenarios ✅
- [ ] ARCHITECTURE.md explains design ✅
- [ ] ADRs document decisions ✅
- [ ] Code comments explain non-obvious logic ✅

### D.5 Optics
- [ ] Git history clean (no "x" commits) ✅
- [ ] Dependencies pinned to minor version ✅
- [ ] Error messages are user-friendly ✅
- [ ] No debug prints in production code ✅
- [ ] Commit messages follow semantic versioning ✅

### D.6 Demo Readiness
- [ ] Can demo in <5 minutes ✅
- [ ] Demo script tested 3+ times ✅
- [ ] Demo video recorded (backup) ✅
- [ ] Pitch deck reviewed by team ✅
- [ ] No errors in demo logs ✅

---

## QUICK STATS

| Category | Count | Status |
|----------|-------|--------|
| Critical Fixes | 3 | 🔴 TODO |
| High-Priority Features | 8 | 🔴 TODO |
| Integration Tests | 7 files, 28 tests | 🔴 TODO |
| Documentation | 3 guides | 🔴 TODO |
| Optional Polish | 6 items | 🟡 OPTIONAL |
| **Total Work** | ~100 hours | **~3 weeks** |

### If you have 1 week:
- ✅ Do Critical Fixes (§A)
- ✅ Do Workspace Roles (§B.1)
- ✅ Do 4 Core Tests: RAG, CodeGraph, Dispatcher, Coordination (§B.2)
- ✅ Do Performance Baseline (§B.2g)
- ⏩ Skip: Polish, docs, Hermes/Admin tests
- **Result**: Ready for pitch to technical audience

### If you have 2 weeks:
- ✅ Do all of 1-week plan
- ✅ Add Hermes + Admin Security Tests (§B.2e, §B.2f)
- ✅ Add Deployment Guide (§B.3)
- ⏩ Skip: Polish, ADRs, Helm chart
- **Result**: Ready for pitch to business + technical

### If you have 3 weeks:
- ✅ Do all of 2-week plan
- ✅ Add SQL injection audit (§C.1)
- ✅ Add ADRs (§C.2)
- ✅ Add CORS validation (§C.1)
- ⏩ Skip: Helm chart, demo video, pitch deck
- **Result**: Production-ready, alpha transparent

### If you have 4 weeks:
- ✅ Do all of 3-week plan
- ✅ Add Demo video + Pitch deck (§C.3)
- ✅ Add Helm chart (§C.2)
- ✅ Add structured error responses (§C.1)
- **Result**: Ready for any investor, any market

---

## HOW TO USE THIS CHECKLIST

1. **Pick your timeline** (1, 2, 3, or 4 weeks)
2. **Copy the relevant section** to a GitHub project / Jira / Notion
3. **Check off items as you complete them**
4. **Celebrate** each section completion 🎉

---

**Print-Friendly Version**: Remove this checklist from your IDE and print it. Keep it on your desk. Check boxes with a pen.

**Digital Version**: Open in GitHub issues, Jira, Notion, or your tracking tool. Update status daily.

**Estimated Completion**: 3 weeks (medium effort, low risk)

Good luck with the pitch! 🚀
