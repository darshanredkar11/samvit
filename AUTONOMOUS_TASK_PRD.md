# PRD: Autonomous Task Decorator (@samvit.task)

**Feature**: `@samvit.task` decorator for automatic task lifecycle management

**Status**: READY TO BUILD

**Target**: All 4 personas (especially Personas A & B)

**Success Metric**: "Setup takes 2 minutes, crashes auto-recover"

---

## 1. What We're Building

A Python decorator that eliminates manual task coordination:

```python
from samvit import task

@task(max_retries=3, timeout=300)
async def process_order():
    """Samvit auto-manages:
    - Task creation (from function name)
    - Task claiming (when called)
    - Retry + timeout (automatic)
    - Task completion (when returns)
    - Failure recovery (auto-retry with new owner)
    """
    return "processed"

# Usage:
result = await process_order()
# Returns: "processed"
# Samvit handled all coordination, retries, timeouts, etc.
```

---

## 2. Core Behaviors

### 2.1: Automatic Task Creation

**When**: First time decorated function is called

**What happens**:
```python
@task
async def process_order():
    return "done"

# First call:
result = await process_order()

# Samvit internally:
# 1. Check if task "process_order" exists in workspace
# 2. If not: CREATE task
#    - task_id = f"process_order_{workspace_id}_{uuid}"
#    - task_name = "process_order" (from function name)
#    - workspace_id = current agent's workspace
#    - status = "pending"
#    - metadata.function = "process_order"
#    - metadata.module = "module_where_defined"
# 3. Return task_id to decorator

# Samvit persists:
INSERT INTO tasks (
  id, workspace_id, name, status, 
  metadata, created_at
) VALUES (...)
```

**Behavior**:
- ✅ Task name auto-derived from function name
- ✅ Workspace auto-derived from calling agent
- ✅ Task created ONCE per agent per function
- ✅ Subsequent calls reuse same task (or create new if completed)

---

### 2.2: Automatic Task Claiming

**When**: Decorated function is called

**What happens**:
```python
result = await process_order()

# Samvit internally:
# 1. Find task for this function
# 2. Attempt atomic claim:
#    SELECT id FROM tasks 
#    WHERE name = 'process_order' 
#      AND status = 'pending'
#      AND workspace_id = ?
#    LIMIT 1
#    FOR UPDATE SKIP LOCKED  -- ATOMIC
# 3. Update task:
#    UPDATE tasks SET 
#      status = 'claimed',
#      claimed_by = agent_id,
#      claimed_at = NOW(),
#      expires_at = NOW() + timeout
#    WHERE id = ?
# 4. Continue to execute
```

**Behavior**:
- ✅ Claim is atomic (CTE + FOR UPDATE)
- ✅ Only one agent gets the task
- ✅ If already claimed: wait / error (configurable)
- ✅ Timeout is set automatically (from decorator parameter)

---

### 2.3: Automatic Execution

**When**: Task is claimed, function body executes

**What happens**:
```python
@task(max_retries=3, timeout=300)
async def process_order():
    return "done"  # Line 5

result = await process_order()

# Samvit internally:
# 1. Function body executes: "done"
# 2. Catch exceptions:
#    - If exception & retries_remaining > 0:
#        → release task (expires_at = NOW())
#        → increment retry_count
#        → return to pool (another agent can claim)
#    - If exception & retries_remaining = 0:
#        → mark task status = 'failed'
#        → store exception in metadata
#    - If success: continue to 3.

# Store execution context:
UPDATE tasks SET 
  metadata.last_execution = {
    started_at: NOW(),
    agent_id: current_agent,
    attempt: retry_count,
  }
```

**Behavior**:
- ✅ Function body executes normally
- ✅ Exceptions are caught (don't crash decorator)
- ✅ Retries handled transparently

---

### 2.4: Automatic Task Completion

**When**: Function returns successfully

**What happens**:
```python
result = await process_order()  # Returns "done"

# Samvit internally:
# 1. Function returned successfully
# 2. Mark task done:
#    UPDATE tasks SET
#      status = 'done',
#      result = result,  -- "done"
#      completed_at = NOW(),
#      expires_at = NULL  -- no timeout
#    WHERE id = ?
# 3. Log to audit:
#    INSERT INTO audit_log (
#      action, task_id, agent_id, 
#      status_before, status_after, result
#    ) VALUES (...)
# 4. Return result to caller
```

**Behavior**:
- ✅ Result stored in task metadata
- ✅ Audit logged automatically
- ✅ Result returned to caller

---

### 2.5: Automatic Failure Recovery

**When**: Function throws exception

**What happens**:
```python
@task(max_retries=3, timeout=300)
async def process_order():
    if random() > 0.5:
        raise Exception("Oops!")  # 50% chance
    return "done"

# Call 1: Exception → Retry 1
# Call 2: Exception → Retry 2
# Call 3: Exception → Retry 3
# Call 4: Success → Done

# Samvit internally (per exception):
# 1. Catch exception
# 2. Check retries_remaining:
#    - If > 0: 
#        → release task (expires_at = NOW() + 5s)
#        → increment attempt counter
#        → store exception in metadata.attempts
#    - If = 0:
#        → mark status = 'failed'
#        → store final exception
#        → DO NOT retry
# 3. Return to pool
```

**Behavior**:
- ✅ Automatic retry on failure
- ✅ Exponential backoff (5s, 10s, 20s, etc.)
- ✅ Max retries honored (then fail)
- ✅ Exception context preserved

---

### 2.6: Automatic Timeout Handling

**When**: Timeout expires (task claimed but not completed)

**What happens**:
```python
@task(max_retries=3, timeout=300)  # 300 seconds = 5 minutes
async def process_order():
    # Hangs for 10 minutes
    await asyncio.sleep(600)
    return "done"

# Samvit background task runs every 10s:
# SELECT * FROM tasks 
# WHERE status = 'claimed'
#   AND expires_at < NOW()
#   AND workspace_id = ?
#
# For each expired task:
# 1. IF task.metadata.attempts < max_retries:
#      → release task (set expires_at = NOW())
#      → increment attempt
#      → another agent can claim & retry
#    ELSE:
#      → mark status = 'failed'
#      → store timeout error
```

**Behavior**:
- ✅ Background cleanup every 10 seconds
- ✅ Stuck tasks auto-released
- ✅ Auto-retry on timeout (respects max_retries)
- ✅ Finally marked 'failed' if max retries exceeded

---

## 3. Configuration Options

```python
@task(
    max_retries=3,        # How many times to retry (default: 3)
    timeout=300,          # Seconds before timeout (default: 300)
    queue="default",      # Task queue name (default: "default")
    auto_retry_timeout=True,  # Retry on timeout (default: True)
    ttl=86400,           # Task lives N seconds (default: 24h)
    metadata={           # Custom metadata (optional)
        "priority": 1,
        "tags": ["auth", "critical"]
    }
)
async def process_order():
    return "done"
```

**Parameters**:
- `max_retries`: 0-10 (0 = no retry)
- `timeout`: 10-3600 seconds
- `queue`: Used for future feature (task routing)
- `auto_retry_timeout`: Bool (auto-retry on timeout or mark failed)
- `ttl`: Task auto-cleanup age (prevents task table growth)
- `metadata`: Custom dict (visible in admin UI + audit log)

---

## 4. API Signature

### Decorator Definition

```python
def task(
    func=None,
    *,
    max_retries: int = 3,
    timeout: int = 300,
    queue: str = "default",
    auto_retry_timeout: bool = True,
    ttl: int = 86400,
    metadata: dict = None,
):
    """Autonomous task coordinator.
    
    Usage:
        @task(max_retries=3, timeout=300)
        async def my_function():
            return "done"
        
        result = await my_function()
    """
    # Implementation
```

### Function Signature Constraints

```python
# MUST be async
@task
async def my_function():  # ✅ Allowed
    return "done"

# CANNOT be sync
@task
def my_function():  # ❌ Will raise TypeError at decorator time
    return "done"

# CAN have arguments (captured in metadata)
@task
async def process_order(order_id: str):  # ✅ Allowed
    return f"processed {order_id}"

# CAN have defaults
@task
async def process_order(order_id: str, priority: int = 1):  # ✅ Allowed
    return f"processed {order_id}"
```

---

## 5. Acceptance Criteria

### Must Have (MVP)

- [ ] `@task` decorator created
- [ ] Auto task creation from function name
- [ ] Atomic task claiming (CTE + FOR UPDATE)
- [ ] Auto task completion on success
- [ ] Auto task retry on exception (with max_retries)
- [ ] Auto timeout handling (background cleanup)
- [ ] Audit logging (all mutations logged)
- [ ] Metadata storage (function args captured)
- [ ] Configuration support (max_retries, timeout, etc.)

### Should Have (Polish)

- [ ] Exponential backoff on retries
- [ ] Telemetry (decorated function latency tracked)
- [ ] Task name customization (if desired)
- [ ] Queue routing (for future multi-queue feature)
- [ ] Admin UI integration (show decorated tasks)

### Nice to Have (Future)

- [ ] Task dependency (Task A must complete before B starts)
- [ ] Automatic deadletter queue (too many retries)
- [ ] Task chaining (return value → next task input)
- [ ] Scheduled tasks (@task + cron)

---

## 6. Database Changes

### New Column: `metadata.function_signature`

```sql
-- Tasks table (existing)
ALTER TABLE tasks 
ADD COLUMN metadata jsonb DEFAULT '{}'::jsonb;

-- New columns in metadata:
-- {
--   "function": "process_order",
--   "module": "agents.order",
--   "args": ["order_123"],
--   "kwargs": {},
--   "attempts": [
--     {"started_at": "2026-06-20T...", "exception": null},
--     {"started_at": "2026-06-20T...", "exception": "timeout"}
--   ],
--   "result": "done",
--   "priority": 1,
--   "tags": ["auth"]
-- }
```

### Background Cleanup Task

```sql
-- New table: task_leases
CREATE TABLE task_leases (
  id UUID PRIMARY KEY,
  task_id UUID REFERENCES tasks(id),
  workspace_id UUID REFERENCES workspaces(id),
  claimed_by UUID REFERENCES agents(id),
  claimed_at TIMESTAMP NOT NULL,
  expires_at TIMESTAMP NOT NULL,
  attempt_count INT DEFAULT 1,
  last_error TEXT
);

-- Index for cleanup query:
CREATE INDEX idx_task_leases_expires 
ON task_leases(workspace_id, expires_at)
WHERE status = 'claimed';
```

### Audit Log Enhanced

```sql
-- audit_log table (existing, but ensure it tracks:)
-- - task_id
-- - agent_id  
-- - action (created, claimed, completed, failed, timeout)
-- - status_before
-- - status_after
-- - metadata (attempt count, retry info, etc.)
```

---

## 7. Implementation Phases

### Phase 1: Core Decorator (Week 1)
- [ ] Create `samvit/decorators.py`
- [ ] Implement @task decorator
- [ ] Task creation + claiming
- [ ] Task completion
- [ ] Basic retry logic

**Lines of code**: 300-400 LOC

### Phase 2: Failure Handling (Week 2)
- [ ] Timeout detection (background task)
- [ ] Auto-release on timeout
- [ ] Retry with exponential backoff
- [ ] Audit logging

**Lines of code**: 200-300 LOC

### Phase 3: Admin UI (Week 3)
- [ ] Show decorated tasks in admin
- [ ] Show retry history
- [ ] Show timeout status
- [ ] Manual retry button

**Lines of code**: 200-300 LOC

### Phase 4: Polish + Docs (Week 4)
- [ ] Performance testing
- [ ] Documentation
- [ ] Examples
- [ ] Migration guide (from manual to decorated)

---

## 8. Testing Requirements

### Unit Tests
- [ ] Decorator accepts async functions
- [ ] Decorator rejects sync functions
- [ ] Task created on first call
- [ ] Task claimed atomically
- [ ] Task completed on success
- [ ] Task retried on exception
- [ ] Timeout triggers release
- [ ] Max retries respected

### Integration Tests
- [ ] Multi-agent concurrent claiming (no double-assign)
- [ ] Failure recovery (agent crashes, task auto-released)
- [ ] Timeout + retry (stuck task auto-released and retried)
- [ ] Audit log complete (all mutations logged)

### Load Tests
- [ ] 1000 decorated functions
- [ ] 100 concurrent claims
- [ ] Timeout cleanup latency <5s

---

## 9. Success Metric: Before vs After

### Before (Manual)

```python
# 6 explicit Samvit calls
task = await create_task("process_order")
try:
    result = await process_order()
    await remember("order_status", result)
    await done(task, result)
except Exception as e:
    await done(task, f"failed: {e}")
    raise
```

### After (Autonomous)

```python
# 1 decorator
@task
async def process_order():
    await remember("order_status", "done")
    return "done"

# Just call it
result = await process_order()
```

**Reduction**: 6 lines → 1 decorator (83% less code)

---

## 10. Rollout Plan

### Stage 1: Internal Testing (Week 1-2)
- Deploy to staging
- Test with 2 agents
- Measure latency (should be <10ms overhead)

### Stage 2: Beta (Week 3)
- Release to Persona B (rapid builders)
- Gather feedback
- Fix edge cases

### Stage 3: GA (Week 4)
- Release to all personas
- Update docs
- Announce feature

---

## 11. Success Definition

Feature is successful when:

- [ ] Setup time: <5 minutes (deploy + use decorator)
- [ ] Learning curve: <1 hour to understand
- [ ] Adoption: 50%+ of new agents use decorator by month 2
- [ ] Reliability: 99.9% task completion (no stuck tasks)
- [ ] Performance: <10ms overhead per decorated function

