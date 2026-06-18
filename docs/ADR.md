# Architecture Decision Records

## ADR-001: Atomic Task Claiming via CTE

**Status**: Accepted  
**Date**: 2026-06-09

### Problem

Task queue must prevent double-assignment: if two workers claim simultaneously, only one should win.

### Solution

Use PostgreSQL CTE + `FOR UPDATE SKIP LOCKED`:

```sql
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
```

### Consequences

- ✅ Guarantees exactly-one assignment across multiple app instances
- ✅ No additional dependencies (no Redis, no locks)
- ⚠️ Postgres-only (not portable to MySQL/SQLite)

---

## ADR-002: Workspace Isolation via workspace_id FK

**Status**: Accepted  
**Date**: 2026-06-18

### Problem

Single deployment must safely isolate data for multiple teams.

### Solution

Add `workspace_id` to all data tables. Application layer enforces scoping on every query.

### Consequences

- ✅ Data-level isolation (can't accidentally expose cross-workspace data)
- ✅ Works with existing query patterns (just add `WHERE workspace_id = $N`)
- ✅ Admin/operator roles with `workspace_id = NULL` see all workspaces
- ⚠️ All new queries must include workspace_id filter

---

## ADR-003: Local Embeddings (no API dependency)

**Status**: Accepted  
**Date**: 2026-06-12

### Problem

Calling external embedding APIs (OpenAI, Cohere) adds latency, cost, and privacy concerns.

### Solution

Use `fastembed` with `BAAI/bge-small-en-v1.5` (384-dim, ~150MB model).

### Consequences

- ✅ Offline-capable (no API keys needed)
- ✅ Deterministic, reproducible results
- ✅ Privacy-preserving (data never leaves the machine)
- ⚠️ Docker image larger (~500MB), cold start ~2s

---

## ADR-004: Event Bus via Redpanda (Kafka API)

**Status**: Accepted  
**Date**: 2026-06-09

### Problem

Agents need async notifications for task completion, memory updates, and system events.

### Solution

Use Redpanda (Kafka-compatible) as the event bus. Events are published but never required for core operations (fire-and-forget).

### Consequences

- ✅ Reliable async delivery with consumer groups
- ✅ Kafka ecosystem compatibility
- ✅ Non-critical path: publish failure is logged, not raised
- ⚠️ Adds operational dependency (Redpanda must be running)

---

## ADR-005: Ethical Guard Layer (Secrets/PII Scanner)

**Status**: Accepted  
**Date**: 2026-06-11

### Problem

Agents might inadvertently store secrets (API keys, tokens) or PII in shared memory, creating a security risk.

### Solution

Add a guard layer that scans all content before storage and after retrieval. 18 regex patterns detect credentials, tokens, keys, and PII. Three response modes: redact, block, or warn.

### Consequences

- ✅ Prevents credential leaks in shared memory
- ✅ Auditable (all violations logged to `guard_violations` table)
- ✅ Configurable per-agent (strict mode for sensitive workspaces)
- ⚠️ False positives possible — entropy check reduces noise
