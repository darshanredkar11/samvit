# Action Items for Samvit v0.2.0 Release

**Status:** 2 issues found, ~15–30 min to fix

---

## Critical Issues

### 🔴 Issue #1: Admin Token Reset Requires Guard (Severity: Medium)

**File:** `samvit/auth.py:164–172`

**Problem:** If `SAMVIT_ADMIN_SECRET` environment variable is not set, the admin token reset endpoint accepts any secret (empty == empty).

**Current Code:**
```python
async def admin_reset_token(handle: str, admin_secret: str) -> str:
    import os
    expected = os.environ.get("SAMVIT_ADMIN_SECRET", "")
    if not expected or not hmac.compare_digest(admin_secret, expected):
        raise PermissionError("Invalid admin secret")
```

**Fix:** Require explicit configuration
```python
async def admin_reset_token(handle: str, admin_secret: str) -> str:
    import os
    expected = os.environ.get("SAMVIT_ADMIN_SECRET", "")
    if not expected:
        raise PermissionError("SAMVIT_ADMIN_SECRET is not configured")
    if not hmac.compare_digest(admin_secret, expected):
        raise PermissionError("Invalid admin secret")
```

**Test:** 
```bash
# Should fail (no secret set)
curl -X POST http://localhost:8765/v1/admin/agents/darshan/reset-token \
  -H "Authorization: Bearer samvit_xxx" \
  -H "Content-Type: application/json" \
  -d '{"admin_secret": ""}'
# Expected: 403 SAMVIT_ADMIN_SECRET is not configured

# Set secret and should work
SAMVIT_ADMIN_SECRET=my-secret
curl -X POST http://localhost:8765/v1/admin/agents/darshan/reset-token \
  -H "Authorization: Bearer samvit_xxx" \
  -d '{"admin_secret": "my-secret"}'
# Expected: 200 with new token
```

---

### 🟡 Issue #2: Commit Message Unclear (Severity: Low)

**Commit:** `f30acfb` with message `x`

**Problem:** Violates semantic versioning; makes git history unreadable for release notes.

**Fix:** Interactive rebase to amend message
```bash
git reset --soft HEAD~1
git commit -m "feat: admin UI, RBAC, rate limiting, audit logging

- Add admin.py with role-based access control
- New migrations for token hashing and admin tables
- Admin React SPA with 6 pages (agents, tasks, guard, settings, graph, login)
- Rate limiting per agent with sliding-window algorithm
- Structured JSON logging with configurable format
- Audit log for all admin mutations
- 10 new test files with ~100+ test cases"
```

---

## Pre-Release Checklist

- [ ] **Fix Issue #1** (5 min) — add guard clause in auth.py
- [ ] **Fix Issue #2** (5 min) — amend commit message
- [ ] **Test admin UI in browser** (5 min)
  - [ ] Run `dev.sh`
  - [ ] Navigate to `http://localhost:5173` (admin-ui dev server)
  - [ ] Login with valid bearer token
  - [ ] Test all 6 pages: Dashboard, Agents, Tasks, Guard, Settings, Graph
  - [ ] Try suspension/role change and verify audit log
- [ ] **Verify rate limiting** (5 min)
  - [ ] Confirm bypass paths work (/health, /ready, /v1/agents/register)
  - [ ] Verify rate limit error returns 429 + Retry-After header
- [ ] **Update documentation** (5 min)
  - [ ] Add note to README about rate limiter scope (in-memory, single-instance only)
  - [ ] Add SAMVIT_ADMIN_SECRET to .env.example with example value
- [ ] **Pin Docker dependencies** (5 min) — update Dockerfile
  - [ ] `python:3.12` → `python:3.12.4-slim-bookworm`
  - [ ] Rebuild and test: `docker compose build --no-cache`
- [ ] **Run full test suite** (2 min)
  ```bash
  pytest tests/ -v --cov=samvit
  ```
- [ ] **Tag release** (1 min)
  ```bash
  git tag -a v0.2.0 -m "Admin UI, RBAC, rate limiting, audit log"
  git push origin v0.2.0
  ```

---

## Tests to Run Locally

```bash
# Unit tests (all should pass)
pytest tests/ -v

# Admin UI build
cd admin-ui && npm run build && cd ..

# Docker image build
docker compose build

# Integration test: register agent, list via admin
docker compose up -d
curl -X POST http://localhost:8765/v1/agents/register \
  -H "Content-Type: application/json" \
  -d '{"handle": "test-agent", "provider": "claude-code"}'
# Extract token from response

curl http://localhost:8765/v1/admin/agents \
  -H "Authorization: Bearer <token-above>" \
  -H "Content-Type: application/json"
# Should return list of agents
```

---

## Timing

- **Total effort:** 15–30 minutes
- **Blocking:** No (all issues are fixable in place)
- **Risk:** Low (only auth edge case and commit message)

---

## Post-Release

After merging to main and tagging v0.2.0:

1. Update CHANGELOG.md with summary of new features
2. Announce on GitHub Discussions / Twitter
3. Start work on v0.3.0 (workspace isolation — highest impact next feature)

---
