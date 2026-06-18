# PostgreSQL vs Redis for Samvit: Architecture Debate

**Decision Point**: Samvit currently uses PostgreSQL 16 + pgvector. Should it use Redis instead?

---

## 1. What Samvit Actually Needs

### Core Requirements

```
✅ Persistent Shared Memory
  - Semantic search (vector embeddings)
  - Key-value store
  - Survives restarts
  
✅ Atomic Task Queue
  - No double-assignment (two agents can't claim same task)
  - Lease-based renewal (long-running tasks)
  - ACID guarantees required
  
✅ Durable Messages
  - Survives agent disconnects + restarts
  - Workspace isolation per message
  - Read-once delivery (no duplicates)
  
✅ Code Graph Storage
  - ~100,000+ nodes per large repo
  - Graph traversal (who calls who)
  - Semantic search over docstrings
  
✅ Multi-Tenancy
  - Workspace isolation (impossible to leak data)
  - Query-level enforcement (not app-level)
  - Composite keys: (workspace_id, entity_id)
  
✅ Scaling
  - 50+ concurrent agents
  - 1000s of tasks
  - Millions of memory embeddings
```

---

## 2. TEAM POSTGRES: Why PostgreSQL is Right

### Argument 1: Atomic Task Claiming (THE KILLER FEATURE)

**The Problem**: Two agents must not claim the same task.

```sql
-- PostgreSQL solution: CTE + row locking
WITH candidate AS (
  SELECT id FROM tasks
  WHERE workspace_id = $1 AND status = 'pending'
  ORDER BY priority DESC
  LIMIT 1
  FOR UPDATE SKIP LOCKED  -- ← ATOMIC
)
UPDATE tasks SET claimed_by = $2
WHERE id IN (SELECT id FROM candidate)
RETURNING *;
```

**Why this matters**:
- Zero probability of double-assignment
- Works across network failures
- Database enforces it, not application code
- Single round trip

**Redis approach**:
```
1. SETNX task:claimed:lock $2  -- try to set lock
2. GET task:claimed:lock       -- check if we won
3. IF we won: UPDATE task SET claimed_by = $2
4. IF we lost: retry from step 1
```

Problems:
- ⚠️ Race condition window (between SETNX and UPDATE)
- ⚠️ Distributed lock complexity (deadlock recovery, timeout)
- ⚠️ Network partition: both clients think they won
- ⚠️ Requires Redlock algorithm (3+ Redis instances)
- ⚠️ Still not atomic at application level

**Verdict**: PostgreSQL wins here. This is *hard* in Redis.

---

### Argument 2: Vector Search + SQL Queries

**Current**: pgvector extension on PostgreSQL

```sql
-- Find code symbols by semantic meaning
SELECT * FROM code_nodes
WHERE workspace_id = $1
  AND embedding <-> $2 < 0.3  -- vector distance
ORDER BY embedding <-> $2
LIMIT 5;

-- Find memories + metadata together
SELECT m.*, r.role
FROM semantic_memory m
JOIN role_metadata r ON m.entity_id = r.id
WHERE m.workspace_id = $1
  AND m.embedding <-> $2 < 0.3
  AND r.role = 'admin'
ORDER BY m.created_at DESC;
```

**Redis approach**:
- Redis Stack offers vector search (RediSearch)
- No complex joins
- No metadata filtering
- Trade-off: much simpler queries

**Verdict**: Tie for simple use cases. PostgreSQL wins for complex queries.

---

### Argument 3: ACID Transactions & Data Integrity

**Example**: Update task + insert audit log atomically

```sql
BEGIN TRANSACTION;
  UPDATE tasks SET status = 'done', result = $1 WHERE id = $2;
  INSERT INTO audit_log (action, task_id, timestamp) VALUES ($3, $2, NOW());
COMMIT; -- ← Both succeed or both roll back
```

**Redis approach**:
- No ACID transactions (until Redis 7.2+ with limited transactions)
- Use Redis transactions: MULTI/EXEC
- But: no rollback on failure
- No atomicity across failures

**Verdict**: PostgreSQL wins. Durability guarantees are stronger.

---

### Argument 4: Multi-Tenancy Enforcement at Database Level

**PostgreSQL**:
```sql
-- Database enforces workspace scoping
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON tasks
  USING (workspace_id = current_setting('app.workspace_id'));

-- Now this query is impossible (RLS blocks it):
SELECT * FROM tasks;  -- returns 0 rows (not your workspace)
```

**Redis approach**:
- No RLS equivalent
- Rely on application code to prepend workspace_id
- Risk: developer forgets `if workspace_id != $1 return 403`
- Security by convention, not enforcement

**Verdict**: PostgreSQL wins. Database enforces isolation.

---

### Argument 5: Operational Simplicity

**PostgreSQL**:
- Single service (postgres process)
- WAL-based durability (recovery tested)
- One backup strategy (pg_dump or WAL archiving)
- Standard tooling (psql, pg_restore, pgAdmin)
- Boring, proven, battle-tested

**Redis**:
- Single service (redis process)
- BUT: RDB + AOF for durability
- RDB unreliable (can lose writes)
- AOF slower, larger disk
- AOF + RDB = operational burden
- Needs Sentinel for HA (3 sentinel instances)
- Needs Cluster for scaling (6+ nodes)

**Verdict**: PostgreSQL wins for "set and forget."

---

## 3. TEAM REDIS: Why Redis Would Be Better

### Argument 1: Speed (Latency Matters)

**Benchmarks** (from Samvit's own perf tests):

```
PostgreSQL:
  - claim task: 45-65ms p99
  - remember: 30-50ms p99
  - explore code: 100-150ms p99

Redis (estimate):
  - claim task: 5-10ms p99 (10x faster)
  - remember: 2-5ms p99 (10x faster)
  - explore code: 20-40ms p99 (5x faster)
```

**Why it matters**:
- Agents waiting for responses = less throughput
- Real-time systems need <100ms latency
- Each saved 50ms = compound on 1000 agents

**Counter-argument**:
- Samvit's current 45ms is fine (agents polling every 2-5 seconds anyway)
- Latency not the bottleneck; throughput is
- Premature optimization

**Verdict**: Redis wins on speed, but PostgreSQL is "fast enough."

---

### Argument 2: Simpler Operational Model

**Redis**:
- One process, single configuration file
- No schema migrations (schemaless)
- No connection pooling complexity
- Works great for caching

**PostgreSQL**:
- Connection pooling (pgBouncer or PgPool)
- Schema migrations (alembic)
- Replication setup complexity
- Memory overhead (each connection = 5-10MB)

**Counter-argument**:
- Schema is feature, not bug (catches errors early)
- Migrations are rare after v0.2.0
- Connection pooling well-understood

**Verdict**: Redis wins on simplicity, but complexity pays off.

---

### Argument 3: Pub/Sub for Real-Time Messaging

**Current Samvit**: HTTP polling (agents ask "any messages for me?")
**Redis Pub/Sub**: Server pushes messages as they arrive

```
Redis approach:
  Agent B: SUBSCRIBE workspace:1:messages
  Agent A: PUBLISH workspace:1:messages "message"
  Agent B: receives immediately (no polling)
```

**Advantage**:
- True real-time (<10ms latency)
- Lower CPU (no polling loop)
- Simpler client code

**Problems**:
- Redis Pub/Sub is fire-and-forget
- If Agent B disconnected when message sent = message lost
- Would need Redis Streams (adds complexity back)

**Verdict**: Redis better for real-time. PostgreSQL requires v0.3.0 WebSocket layer.

---

### Argument 4: Scaling Out Easier

**Redis Cluster**:
- Sharding built-in (6+ nodes)
- Linear scaling (add nodes, data redistributes)
- No complex replication

**PostgreSQL Streaming Replication**:
- Master + replicas (but replicas read-only)
- Sharding requires application layer (Citus or pgPartman)
- More operational work

**Counter-argument**:
- Samvit doesn't need scale-out yet (1 machine fine)
- When it does, Citus extension is mature
- Redis scaling = network complexity

**Verdict**: Redis wins for future scale-out. PostgreSQL fine for now.

---

## 4. The Hybrid Option: PostgreSQL + Redis

**What if we used both?**

```
┌─────────────────────────────────────────┐
│  Application Layer (Samvit server)      │
└─────────────────┬───────────────────────┘
                  │
        ┌─────────┴──────────┐
        │                    │
    PostgreSQL          Redis Cache
    ├─ Durable state    ├─ Session cache
    ├─ Task queue       ├─ Message buffer
    ├─ Memory storage   ├─ Real-time updates
    └─ Code graph       └─ Pub/Sub
```

### Advantages:
✅ PostgreSQL for durability + atomicity
✅ Redis for speed + real-time
✅ Best of both worlds

### Disadvantages:
❌ Operational complexity doubles
❌ Cache invalidation problems
❌ Network partitions harder to reason about
❌ "Two databases" = two backup strategies
❌ Synchronization bugs (Redis ahead of Postgres)

**Verdict**: Over-engineered for Samvit's current scope.

---

## 5. Decision Matrix

| Requirement | PostgreSQL | Redis | Winner |
|---|---|---|---|
| Atomic task claiming | ✅✅✅ Built-in | ⚠️ Complex | **PostgreSQL** |
| Vector search | ✅ pgvector | ⚠️ RediSearch | **PostgreSQL** |
| Multi-tenancy isolation | ✅✅ RLS | ⚠️ App-level | **PostgreSQL** |
| Latency <50ms | ✅ 45ms p99 | ✅✅ 5ms p99 | **Redis** |
| Durability | ✅✅ WAL proven | ⚠️ RDB/AOF | **PostgreSQL** |
| ACID transactions | ✅✅✅ Full | ⚠️ Limited | **PostgreSQL** |
| Real-time pub/sub | ⚠️ v0.3.0 | ✅✅✅ Native | **Redis** |
| Operational simplicity | ✅ 1 way | ✅ 1 way | **Tie** |
| Scaling to 1M agents | ⚠️ Needs Citus | ✅ Cluster | **Redis** |
| Code graph queries | ✅✅ SQL joins | ⚠️ No joins | **PostgreSQL** |

**Score**: PostgreSQL 7 wins, Redis 2 wins, Tie 1

---

## 6. The Hard Truth: It Depends On These Decisions

### If Samvit stays HTTP polling, PostgreSQL is right
- Latency not critical (agents poll every 2-5 seconds anyway)
- Atomicity critical (task claiming must work)
- Durability critical (messages survive restarts)
- **Verdict**: Keep PostgreSQL ✅

### If Samvit becomes real-time WebSocket, Redis becomes viable
- Low latency required (<100ms)
- Atomicity still required (use Redlock or Lua scripts)
- Durability still required (use Redis Streams)
- **Verdict**: Consider Redis + PostgreSQL hybrid ⚠️

### If Samvit scales to 1000+ concurrent agents, consider sharding
- PostgreSQL: Citus extension (managed sharding)
- Redis: Native clustering
- **Verdict**: PostgreSQL still fine with Citus

---

## 7. What Would Break If We Switched to Redis

### Loss of Safety
1. **Task double-assignment**
   - Race condition: two agents both claim same task
   - Requires complex Redlock implementation
   - Still not atomic at application level

2. **Data corruption**
   - No ACID transactions
   - Concurrent updates lose data
   - Example: two agents both update task result

3. **Multi-tenancy leaks**
   - No RLS to prevent developer mistakes
   - App bug could expose team A's data to team B
   - PostgreSQL RLS catches this automatically

### Loss of Features
1. **Complex queries**
   - Can't join tasks + audit_log
   - Can't filter memories by metadata + workspace
   - Would need denormalization (duplication)

2. **Vector search with filters**
   - `SELECT * FROM code_nodes WHERE workspace_id = $1 AND language = $2 AND embedding <-> $3 < 0.3`
   - Redis: would need custom script

### Loss of Durability
1. **RDB format**
   - Single point of failure (RDB can be corrupted)
   - Window between RDB saves = data loss
   - AOF solving this = 5x slower

2. **No recovery guarantees**
   - PostgreSQL WAL proven recovery
   - Redis depends on admin knowing RDB/AOF

---

## 8. PostgreSQL's "Hidden Wins"

### Win 1: Workspace Isolation at Query Level
```sql
-- Once RLS is set up, this is IMPOSSIBLE:
SELECT * FROM other_team_tasks;  -- 0 rows (RLS blocks)

-- Redis: Easy to accidentally do:
redis.get(f"tasks:{workspace_id}:pending")
# Oops, forgot to prepend workspace_id
redis.get("tasks:pending")  -- returns all teams
```

### Win 2: Schema as Documentation
```sql
CREATE TABLE tasks (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  status task_status NOT NULL,  -- enum, validated at DB
  expires_at TIMESTAMP,
  ...
);
-- This documents the contract better than any comment
```

### Win 3: Index Hints Are Free
```sql
-- PostgreSQL: Planner handles it
SELECT * FROM code_nodes 
WHERE workspace_id = $1 AND embedding <-> $2 < 0.3;
-- ✅ Uses HNSW index automatically

-- Redis: Depends on user knowing to use RediSearch
FTINDEX code_nodes ...  -- Manual setup
```

---

## 9. Final Verdict

### **POSTGRESQL IS THE RIGHT CHOICE FOR SAMVIT**

**Why**:

1. **Atomic task claiming is non-negotiable**
   - PostgreSQL solves this elegantly
   - Redis makes it hard and error-prone
   - This is core to Samvit's value prop

2. **Multi-tenancy isolation must be at database level**
   - RLS prevents developer mistakes
   - Redis relies on convention
   - One bug leaks other teams' data

3. **Durability guarantees are stronger**
   - WAL-based recovery proven
   - RDB/AOF less reliable
   - Agents depend on messages surviving

4. **You're not latency-bound**
   - Current 45ms is fine
   - Agents polling every 2-5 seconds anyway
   - Real bottleneck is network, not database

5. **Complexity isn't preventing adoption**
   - Single Docker container, out of the box
   - Connection pooling built-in
   - Schema migrations are rare

### But PostgreSQL Does Have One Real Gap

**Real-time messaging** (v0.3.0):
- Current HTTP polling is adequate for MVP
- WebSocket push doesn't require Redis
- Use PostgreSQL NOTIFY for pub/sub
- Or add Redis Cache Layer specifically for subscriptions

### Recommendation

**Status quo**: Keep PostgreSQL. ✅

**For v0.3.0**: Don't add Redis. Instead:
1. Use PostgreSQL `LISTEN/NOTIFY` for pub/sub
2. Add WebSocket layer to Samvit
3. Keep all durability in PostgreSQL
4. Add Redis caching ONLY if latency testing shows bottleneck

---

## 10. Dangerous Quote to Avoid

> "Let's use Redis because it's faster"

**Danger**: Speeds up wrong part. Samvit's bottleneck is:
- Network latency (agents polling)
- Not database latency
- Redis saves 40ms here, agents lose 2000ms in polling anyway

**Right reason to consider Redis**:
> "We need real-time push notifications and can build on top of durability guarantees PostgreSQL provides"

Then: PostgreSQL (durable) + Redis (ephemeral pub/sub) + WebSocket (push)

---

## Conclusion

**PostgreSQL wins this debate decisively.**

Redis is excellent for caching, sessions, and real-time dashboards. But Samvit's core requirements (atomic task assignment, durable shared state, multi-tenancy isolation) all favor PostgreSQL.

The day Samvit needs <50ms latency across thousands of agents: revisit this. Until then, PostgreSQL is simpler, safer, and more mature.

