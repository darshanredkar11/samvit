# Samvit — Failure & Fault Analysis v0.1

> Exhaustive enumeration of input permutations, failure modes, race conditions,
> and cascading failures across every MVP function.
> Written before implementation to drive defensive coding and test design.

---

## How to read this document

Each section covers one function or subsystem. For every function:

1. **Input permutation matrix** — every valid/invalid combination of params
2. **Failure modes** — what can go wrong and why
3. **Race conditions** — concurrent-call scenarios
4. **Cascading failures** — what breaks downstream when this breaks

Severity labels: 🔴 Critical (data loss / silent corruption) · 🟠 High (function broken) · 🟡 Medium (degraded) · 🟢 Low (cosmetic / recoverable)

---

## 1. Agent Registration — `POST /v1/agents/register`

### Input permutations

| # | handle | provider | Expected result |
|---|---|---|---|
| 1 | `"darshan"` | `"claude"` | ✅ 201 + token |
| 2 | `"darshan"` | `"claude"` (again) | 409 conflict |
| 3 | `""` (empty) | `"claude"` | 400 bad request |
| 4 | `null` | `"claude"` | 400 bad request |
| 5 | `"darshan"` | `""` | 400 bad request |
| 6 | `"darshan"` | `null` | 400 bad request |
| 7 | `"DARSHAN"` | `"claude"` | ❓ Is this a duplicate of `"darshan"`? Case sensitivity undefined |
| 8 | `"darshan; DROP TABLE agents"` | `"claude"` | Must be safely parameterised |
| 9 | `"a"` (1 char) | `"x"` | Should accept — no min-length defined in spec |
| 10 | 500-char handle | `"claude"` | Should reject — no max-length defined in spec |
| 11 | Handle with spaces `"dar shan"` | `"claude"` | ❓ Undefined — spaces in handle break CLI UX |
| 12 | Handle with `@`, `/`, `.` | `"claude"` | ❓ Breaks Redpanda topic name `messages.darshan` |
| 13 | Body missing entirely | — | 400 bad request |
| 14 | Extra unknown fields in body | — | Should ignore (be lenient) |

### Failure modes

| Failure | Severity | Trigger | Symptom | Missing safeguard |
|---|---|---|---|---|
| Token returned then DB write fails | 🔴 | Postgres crash between token gen and INSERT | Token exists in client, not in DB — every subsequent request is 401 forever | Token should only be returned after confirmed DB commit |
| bcrypt blocks event loop | 🟠 | High concurrency registrations | Server hangs; all MCP requests queue up | bcrypt must run in thread pool executor, not async directly |
| Token entropy too low | 🔴 | Bad random source / short token | Brute-forceable tokens | Spec says "random64" — must be `secrets.token_urlsafe(48)` minimum |
| Handle case collision | 🟠 | `"Darshan"` and `"darshan"` register separately | Two agents, ambiguous recipient for `say --to darshan` | Normalise handles to lowercase on registration |
| Special chars in handle break topic name | 🟠 | Handle contains `.`, `/`, space | Redpanda topic `messages.dar.shan` has unintended sub-topic semantics | Validate handle: `^[a-z0-9_-]{1,64}$` |

### Race conditions

- **Double registration:** Two requests for the same handle arrive simultaneously. The `UNIQUE` constraint on `handle` catches one, but the 409 response must be indistinguishable from a normal conflict — no token should be emitted for the loser.

---

## 2. Token Rotation — `POST /v1/agents/rotate`

### Input permutations

| # | Auth header | Expected result |
|---|---|---|
| 1 | Valid current token | ✅ 200 + new token, old invalidated |
| 2 | Already-rotated (old) token | 401 |
| 3 | Missing header | 401 |
| 4 | Malformed `Bearer` prefix | 401 |
| 5 | Token for deleted agent (future) | 401 |
| 6 | Rapid double-rotation (concurrent) | ❓ Race — see below |

### Failure modes

| Failure | Severity | Trigger | Symptom |
|---|---|---|---|
| New token written before old invalidated | 🔴 | Crash between two DB writes | Both old and new tokens valid simultaneously |
| Rotation succeeds but client never receives new token | 🟠 | Network drop after DB commit | Agent locked out permanently — needs admin reset path |
| No admin reset path defined | 🟠 | Lost token | Agent can never be re-authenticated — spec gap |

### Race conditions

- Two concurrent rotation requests with the same token: first wins, second should get 401. Needs `SELECT FOR UPDATE` on the agent row or a single atomic `UPDATE … RETURNING`.

---

## 3. `remember`

### Input permutation matrix

| # | content | key | namespace | metadata | Expected |
|---|---|---|---|---|---|
| 1 | `"JWT tokens expire in 24h"` | — | — | — | ✅ vector write only |
| 2 | `"JWT tokens expire in 24h"` | `"auth.expiry"` | — | — | ✅ vector + KV upsert |
| 3 | `"text"` | `"k"` | `"global"` | `{"tag":"arch"}` | ✅ full write |
| 4 | `""` (empty content) | — | — | — | ❓ Should reject — embedding of empty string is meaningless |
| 5 | `null` | — | — | — | 400 |
| 6 | 100,000-char content | — | — | — | ❓ No max size defined — embedding model has token limit |
| 7 | `"text"` | `""` (empty key) | — | — | ❓ Undefined — empty key stored or rejected? |
| 8 | `"text"` | — | `""` | — | ❓ Empty namespace — should default or reject? |
| 9 | `"text"` | — | `"other_agent"` | — | ❓ Can I write to another agent's namespace? |
| 10 | Same key, different content (upsert) | `"k"` | — | — | ✅ KV updated, new vector row added (old not removed) |
| 11 | Unicode / emoji content | — | — | — | Must not break embedding pipeline |
| 12 | Binary / base64 content | — | — | — | Should store as-is but embedding will be garbage |
| 13 | Metadata with deeply nested JSON | — | — | `{"a":{"b":{"c":...}}}` | ❓ No depth limit defined |

### Failure modes

| Failure | Severity | Trigger | Symptom |
|---|---|---|---|
| Embedding succeeds, DB write fails | 🔴 | Postgres down after embedding | Content embedded in memory (RAM) but never persisted — silent loss |
| KV upsert succeeds, vector write fails | 🟠 | Mid-transaction crash | `recall` by key works, semantic recall misses it |
| Embedding model not loaded at startup | 🟠 | Cold start race | First `remember` fails; model loads lazily but request already failed |
| Content too long for embedding model | 🟠 | >512 tokens (MiniLM limit) | Model silently truncates — stored embedding represents partial content |
| KV and vector writes not in same transaction | 🔴 | Crash between the two | Partial state — key exists but no vector, or vice versa |
| Namespace write isolation not enforced | 🟠 | Agent writes to `"global"` when they should only write to own namespace | Pollutes shared memory — spec says default is caller's namespace, but doesn't restrict cross-namespace writes |
| Upsert on KV does not clean up old vector row | 🟡 | Re-remembering same key | `recall` returns stale + new duplicate results for same key |

### Race conditions

- Two agents simultaneously `remember` with the same key in `"global"` namespace: last write wins for KV, but **both** vector rows are kept. `recall` will return duplicates.

---

## 4. `recall`

### Input permutation matrix

| # | query | key | namespace | limit | min_score | Expected |
|---|---|---|---|---|---|---|
| 1 | `"auth flow"` | — | — | — | — | ✅ vector search, own namespace |
| 2 | `"auth flow"` | — | `"global"` | — | — | ✅ vector search, global namespace |
| 3 | — | `"auth.expiry"` | — | — | — | ✅ KV lookup |
| 4 | `"auth flow"` | `"auth.expiry"` | — | — | — | ❓ Both set — which takes priority? Spec says key bypasses vector; confirm |
| 5 | — | — | — | — | — | 400 — nothing to search |
| 6 | `""` | — | — | — | — | ❓ Empty query — embed empty string? Reject? |
| 7 | `"auth"` | — | — | `0` | — | ❓ limit=0 — return nothing or error? |
| 8 | `"auth"` | — | — | `-1` | — | 400 invalid |
| 9 | `"auth"` | — | — | `1000` | — | ❓ No max limit defined — could be slow |
| 10 | `"auth"` | — | — | — | `0.0` | Returns everything regardless of score |
| 11 | `"auth"` | — | — | — | `1.0` | Returns only exact matches (near-impossible) |
| 12 | `"auth"` | — | — | — | `1.1` | 400 — out of range |
| 13 | `"auth"` | — | `"nonexistent"` | — | — | ✅ Empty results, not error |
| 14 | `"auth"` | `"missing_key"` | — | — | — | ❓ 404 or empty result? Inconsistent with vector behaviour |
| 15 | `"auth"` | — | `"other_agent"` | — | — | ❓ Can I read another agent's namespace? Spec gap |

### Failure modes

| Failure | Severity | Trigger | Symptom |
|---|---|---|---|
| Vector index not yet built (cold start) | 🟠 | `remember` called before `ivfflat` index has enough rows (requires ≥100 rows for IVFFlat) | `recall` returns 0 results even though data exists — IVFFlat needs minimum rows to be useful; use sequential scan fallback |
| KV miss treated differently from vector miss | 🟡 | Key not found | Inconsistent API — one returns 404, other returns `[]` |
| Cross-namespace reads not gated | 🟠 | Agent reads `"darshan"` namespace without being Darshan | Privacy leak between agents |
| Stale duplicate vectors from repeated `remember` on same key | 🟡 | See §3 race | `recall` returns same content twice with slightly different scores |
| Query embedding fails (model error) | 🟠 | OOM, corrupted model | `recall` returns 500 — no fallback |

---

## 5. `claim`

### Input permutation matrix

| # | tags | task_id | Queue state | Expected |
|---|---|---|---|---|
| 1 | — | — | Tasks available | ✅ Returns highest-priority pending task |
| 2 | — | — | Queue empty | `{ "task": null }` |
| 3 | `["backend"]` | — | Tasks with/without tag | Returns only tasks matching tag |
| 4 | `["backend", "auth"]` | — | Tasks with one or both tags | OR match — returns tasks with either tag |
| 5 | `["nonexistent"]` | — | No matching tasks | `{ "task": null }` |
| 6 | — | `"uuid-of-pending-task"` | Task exists and is pending | ✅ Claims specific task |
| 7 | — | `"uuid-of-claimed-task"` | Task already claimed | ❓ 409 or `{ "task": null }`? Undefined |
| 8 | — | `"uuid-of-done-task"` | Task done | ❓ 404 or 409? Undefined |
| 9 | — | `"nonexistent-uuid"` | — | 404 |
| 10 | `["tag"]` | `"specific-uuid"` | Both set | ❓ Which takes priority? Spec gap |
| 11 | — | — | 1000 pending tasks | Performance — must use index on `(status, priority, created_at)` |
| 12 | Caller already has 5 active claims | — | — | ❓ No max-claims-per-agent defined — can one agent hoard all tasks? |

### Failure modes

| Failure | Severity | Trigger | Symptom |
|---|---|---|---|
| Missing index on `(status, priority, created_at)` | 🟠 | Large task table | Full table scan on every `claim` — gets slow at scale |
| `FOR UPDATE SKIP LOCKED` not used | 🔴 | Concurrent `claim` calls | Two agents claim the same task — double work, corrupted state |
| `claim_token` not cryptographically random | 🟠 | Weak RNG | Token guessable — agent can `done` a task it didn't claim |
| No max open claims per agent | 🟡 | One agent crashes with 50 tasks claimed | All 50 tasks locked for 30 min; team blocked |
| `claimed_at` timezone inconsistency | 🟡 | Server and client in different timezones | Expiry logic miscalculates timeout window |
| `claim_timeout` not configurable per task | 🟡 | Long-running task takes >30 min legitimately | Task re-released while agent is still working on it |

### Race conditions

- **Thundering herd:** 10 agents all call `claim` simultaneously. `FOR UPDATE SKIP LOCKED` handles this correctly — each gets a different task or null. Must be verified under load.
- **Expiry during active work:** Background cleanup runs at minute 30; agent calls `done` at minute 30+1 second. `claim_token` is now cleared. `done` gets a 403. Work is lost. → Need a grace period or extend-claim mechanism.

---

## 6. `done`

### Input permutation matrix

| # | task_id | claim_token | result | status | Expected |
|---|---|---|---|---|---|
| 1 | valid | correct token | `{}` | `"done"` | ✅ |
| 2 | valid | correct token | — | — | ✅ (status defaults to "done") |
| 3 | valid | wrong token | — | — | 403 |
| 4 | valid | correct token | — | `"failed"` | ✅ marks failed |
| 5 | valid | correct token | — | `"cancelled"` | ❓ Spec reserves but doesn't define handling |
| 6 | valid | correct token | — | `"pending"` | 400 — cannot revert to pending via `done` |
| 7 | valid | correct token | — | `"claimed"` | 400 — nonsensical |
| 8 | `"uuid-of-pending-task"` | any | — | — | 403/409 — task not claimed by anyone |
| 9 | `"uuid-of-done-task"` | old token | — | — | 409 — already done |
| 10 | `"uuid-of-expired-claim"` | correct token | — | — | 403 — token cleared by expiry job |
| 11 | nonexistent uuid | — | — | — | 404 |
| 12 | valid | correct token | 50MB JSON result | — | ❓ No result size limit — could bloat DB |
| 13 | valid | correct token | non-JSON result | — | 400 |

### Failure modes

| Failure | Severity | Trigger | Symptom |
|---|---|---|---|
| Redpanda publish fails after DB update | 🟠 | Redpanda down | Task marked done in DB but no `task.completed` event published — downstream listeners miss it |
| DB update succeeds, Redpanda publish fails, no retry | 🟠 | Network blip | Silent event loss |
| `done` called on expired claim (race with cleanup) | 🟠 | Work finishes just after 30-min expiry | 403 error even though work was completed correctly — result discarded |
| Large `result` bloats tasks table | 🟡 | Agents storing full file contents in result | Slow queries, storage growth |
| No idempotency — double `done` call | 🟡 | Network retry | Second call gets 409 but first may have been logged twice in event bus |

---

## 7. `say`

### Input permutation matrix

| # | to | topic | body | metadata | Expected |
|---|---|---|---|---|---|
| 1 | `"darshan"` | — | `"hello"` | — | ✅ directed message |
| 2 | `null` | — | `"hello"` | — | ✅ broadcast |
| 3 | — (omitted) | — | `"hello"` | — | ✅ broadcast (same as null) |
| 4 | `"darshan"` | `"reviews"` | `"hello"` | — | ✅ directed + topic label |
| 5 | `null` | `"alerts"` | `"hello"` | — | ✅ broadcast on topic |
| 6 | `"nonexistent_agent"` | — | `"hello"` | — | ❓ 404 or silent send to void? Spec gap |
| 7 | `"darshan"` | — | `""` | — | 400 — empty body |
| 8 | `"darshan"` | — | `null` | — | 400 |
| 9 | Self `"darshan"` → `"darshan"` | — | `"note to self"` | — | ✅ should work — useful for self-notes |
| 10 | `"darshan"` | — | 10MB body | — | ❓ No size limit defined |
| 11 | `"darshan"` | — | `"hello"` | deeply nested | ❓ No metadata schema validation |

### Failure modes

| Failure | Severity | Trigger | Symptom |
|---|---|---|---|
| Message written to DB, Redpanda publish fails | 🟠 | Redpanda down | Message stored, but real-time subscriber misses it — only recoverable via `read` polling |
| `to` handle not validated against agents table | 🟠 | Sending to nonexistent agent | Message stored forever, nobody ever reads it — silent void |
| No delivery confirmation | 🟡 | Always | Sender has no way to know if message was received |
| Broadcast storms | 🟡 | Agent in loop calling `say` broadcast | All agents' `read` queues flood |
| Redpanda topic auto-creation disabled in production | 🟠 | Hardened Redpanda config | First `say` fails with unknown topic error |

---

## 8. `read`

### Input permutation matrix

| # | topic | from | limit | mark_read | Expected |
|---|---|---|---|---|---|
| 1 | — | — | — | — | ✅ all unread messages to caller + broadcasts |
| 2 | `"reviews"` | — | — | — | ✅ filtered by topic |
| 3 | — | `"sachin"` | — | — | ✅ filtered by sender |
| 4 | — | — | `5` | — | ✅ first 5 |
| 5 | — | — | — | `false` | ✅ peek — messages not marked read |
| 6 | — | — | `0` | — | ❓ limit=0 — empty result or error? |
| 7 | — | — | `-1` | — | 400 |
| 8 | — | — | `10000` | — | ❓ No max limit — could return millions of rows |
| 9 | `"nonexistent"` | — | — | — | ✅ empty results |
| 10 | — | `"nonexistent_agent"` | — | — | ❓ 404 or empty results? |
| 11 | — | — | — | — | Called twice in a row | Second call returns nothing (messages marked read) |
| 12 | — | — | — | `false` | Called twice | Both calls return same messages ✅ |

### Failure modes

| Failure | Severity | Trigger | Symptom |
|---|---|---|---|
| `read_by` array grows unboundedly | 🟡 | Message broadcast to 1000 agents | `read_by UUID[]` column for a popular broadcast message becomes huge; queries slow |
| Mark-read not atomic with fetch | 🟠 | Two concurrent `read` calls by same agent | Both calls return same messages; both try to append to `read_by`; one wins, one silently fails to mark |
| No pagination beyond `limit` | 🟡 | Agent has 10,000 unread messages | Only first 20 accessible; no `offset` or cursor |
| Broadcast messages never deleted | 🟡 | High message volume | `messages` table grows forever; no TTL or archival defined |
| `from` filter accepts nonexistent handle | 🟡 | Typo in handle | Silently returns empty — no error to alert caller |

---

## 9. Cross-Cutting Failures

### 9.1 Authentication

| Scenario | Expected | Risk |
|---|---|---|
| Valid token, correct agent | ✅ pass | — |
| Valid token format, agent deleted (future) | 401 | Token must be validated against DB, not just format-checked |
| Token passed in URL query string | ❓ | Should be rejected — tokens in URLs appear in server logs |
| Token leaked in logs | 🔴 | Never log the raw `Authorization` header |
| Timing attack on token comparison | 🟠 | Use `hmac.compare_digest`, not `==` |
| Token with whitespace padding | 🟡 | `" samvit_abc"` — strip before compare |

### 9.2 PostgreSQL Failures

| Failure | Severity | Affected functions | Mitigation |
|---|---|---|---|
| Connection pool exhausted | 🔴 | All | All requests fail with 500; set pool size + timeout |
| Postgres restart during request | 🟠 | Any write | asyncpg raises; must catch and return 503 |
| pgvector extension not installed | 🔴 | remember, recall | Server crashes at migration time — must check at startup |
| ivfflat index requires ≥100 rows | 🟠 | recall | Returns 0 results on fresh install; fall back to sequential scan when row count < 100 |
| Disk full | 🔴 | All writes | Postgres stops accepting writes; no alerting defined |
| Long-running transaction holds locks | 🟠 | claim | Blocks all other claimers |

### 9.3 Redpanda Failures

| Failure | Severity | Affected functions | Mitigation |
|---|---|---|---|
| Redpanda not started | 🟠 | say, done (event publish) | Server should start but mark event publishing as degraded; writes still work |
| Redpanda broker unavailable mid-request | 🟠 | say, done | Publish fails silently; need retry queue or dead-letter |
| Topic partition full | 🟡 | say | Messages dropped; define retention policy |
| Consumer lag (agent not reading) | 🟡 | say | Messages pile up; Redpanda consumer group offset stalls |
| No message TTL | 🟡 | All | Old messages retained forever; disk fills |

### 9.4 Embedding Model Failures

| Failure | Severity | Trigger | Symptom |
|---|---|---|---|
| Model not downloaded at startup | 🔴 | Fresh Docker build without cache | `remember` and `recall` both fail |
| Model download at startup blocks request handling | 🟠 | Slow network | Server appears up but all memory tools fail for ~30s |
| OOM during embedding | 🟠 | Many concurrent `remember` calls | Process killed; server restarts |
| Model produces NaN embeddings | 🔴 | Corrupt input / edge case | Stored as NaN vector; `recall` returns garbage scores or errors |
| Embedding batch size not limited | 🟠 | Long content | Single embedding call times out |

### 9.5 Startup / Cold Start

| Scenario | Risk |
|---|---|
| Server starts before Postgres is ready | Migrations fail; server crashes. Docker health checks mitigate but window exists. |
| Server starts before Redpanda is ready | Event publishing fails on first requests |
| Migration runs twice (multiple replicas) | Must use advisory lock or idempotent `CREATE TABLE IF NOT EXISTS` |
| Partial migration (crash mid-script) | Table exists but incomplete; next startup re-runs from version table — must be atomic per migration |

### 9.6 Data Consistency

| Scenario | Severity | Description |
|---|---|---|
| KV write + vector write not in one transaction | 🔴 | If Postgres crashes between the two writes in `remember`, partial state exists |
| Task status transitions not validated server-side | 🟠 | Client could call `done` on a `pending` task if `claim_token` check is bypassed |
| Agent deletes self (future) | 🟠 | Orphaned messages, tasks with `from_agent = NULL` FK violation — no cascade delete defined |
| UUID collisions | 🟢 | `gen_random_uuid()` — statistically impossible but not impossible |

---

## 10. Security Failure Surface

| Attack | Severity | Description | Mitigation |
|---|---|---|---|
| Token brute-force | 🟠 | Short token space | Use 48-byte `secrets.token_urlsafe` minimum |
| SQL injection | 🔴 | Unsanitised params | asyncpg parameterised queries — never f-string SQL |
| Handle injection into Redpanda topic name | 🟠 | `handle = "foo.bar"` → topic `messages.foo.bar` | Validate handle on registration: `^[a-z0-9_-]+$` |
| Namespace traversal | 🟠 | Agent sets `namespace = "darshan"` to read/write Darshan's memory | Server must enforce namespace = own handle for private writes; global = shared write |
| Message flooding | 🟡 | Agent in loop calls `say` broadcast | No rate limiting in MVP — post-MVP item |
| Claim token replay | 🟠 | Captured `claim_token` reused after task is done | Token is cleared on `done` — but only if code is correct |
| Large payload DoS | 🟡 | 100MB body in `say` or `remember` | No max payload size in spec — add to nginx/server config |

---

## 11. Decisions — Locked ✅

All gaps resolved. These are the binding implementation rules.

| # | Issue | Decision | Rule |
|---|---|---|---|
| 1 | Handle validation | ✅ Locked | Regex `^[a-z0-9_-]{1,64}$` enforced at registration; reject 400 otherwise |
| 2 | Handle case | ✅ Locked | Normalise to lowercase on registration; store and match lowercase only |
| 3 | Cross-namespace permissions | ✅ Locked | Any agent may read/write `global`; private namespace = own handle only; all writes tagged with `agent_id` |
| 4 | `say` to unknown handle | ✅ Locked | 404 — validate handle against `agents` table before inserting |
| 5 | `recall` KV miss | ✅ Locked | Return `{ "results": [] }` — never 404 for a miss; 404 only for malformed request |
| 6 | KV + vector atomicity | ✅ Locked | Single `BEGIN … COMMIT` wrapping both writes in `remember`; either both land or neither does |
| 7 | IVFFlat cold start | ✅ Locked | If `semantic_memory` row count < 100, use sequential scan (`ORDER BY embedding <=> $1`); switch to index automatically above threshold |
| 8 | Claim expiry grace | ✅ Locked | Cleanup resets tasks at `claimed_at + claim_timeout + 5min`; no new API needed |
| 9 | Payload size limits | ✅ Locked | `content` ≤ 32 KB · `body` ≤ 64 KB · `result` ≤ 1 MB; enforced server-side before any DB/embedding call |
| 10 | Token in logs | ✅ Locked | Middleware strips `Authorization` header before any log call; DEBUG level included |
| 11 | `read_by` scaling | ✅ Locked | Replace `read_by UUID[]` column with `message_reads(message_id, agent_id, read_at)` join table |
| 12 | Admin token reset | ✅ Locked | `POST /v1/admin/agents/{handle}/reset` authenticated by `SAMVIT_ADMIN_SECRET` env var; returns new token |
| 13 | Redpanda failure handling | ✅ Locked | Publish wrapped in `try/except`; on failure log `WARNING` with event details; request still returns 200 |
| 14 | Embedding startup | ✅ Locked | Model loaded eagerly in FastAPI `lifespan()`; if load fails, server exits non-zero and Docker restarts it |
| 15 | Tags filter semantic | ✅ Locked | OR filter: task matches if it contains **any** supplied tag; enforced with `tags && $1::text[]` in SQL |

---

## 12. Schema Amendments Required (from decisions above)

The following schema changes must be applied to `001_initial.sql` before coding begins:

```sql
-- Decision #11: replace read_by array with join table
-- Remove read_by UUID[] from messages; add:
CREATE TABLE message_reads (
    message_id  UUID REFERENCES messages(id) ON DELETE CASCADE,
    agent_id    UUID REFERENCES agents(id) ON DELETE CASCADE,
    read_at     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (message_id, agent_id)
);

-- Decision #8: claim_timeout grace — no schema change needed
-- Cleanup query becomes:
--   WHERE status = 'claimed'
--   AND claimed_at + claim_timeout + INTERVAL '5 minutes' < now()

-- Decision #7: IVFFlat sequential scan fallback — no schema change
-- Handled in application code (db.py / recall tool)

-- Decision #9: payload size limits — enforced in application layer, not DB
-- (PostgreSQL TEXT has no practical size limit; limits enforced before insert)
```

---

*Failure Analysis v0.2 — all §11 decisions locked. Ready for implementation.*
