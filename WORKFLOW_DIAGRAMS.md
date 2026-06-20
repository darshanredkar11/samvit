# Workflow Diagrams: Manual vs Autonomous

## Current (Manual) Workflow

### Scenario: Alice creates task → Bob claims and works → Charlie verifies

```
CURRENT (Manual Coordination)
═══════════════════════════════════════════════════════════════

Alice's Agent                 Samvit Server              Bob's Agent
    │                              │                         │
    │──── create_task() ──────────►│                         │
    │    "Implement auth"          │                         │
    │◄─── task_id: 123 ─────────────                         │
    │                              │                         │
    │─── remember() ──────────────►│                         │
    │    "Auth spec"               │                         │
    │◄─── saved ────────────────────                         │
    │                              │                         │
    │ (Alice's session ends)       │                         │
    │                              │                         │
    │                              │    ◄── claim_task() ────│
    │                              │        (hopes for right  │
    │                              │         one)            │
    │                              ├─── task 123 claimed ────►
    │                              │                         │
    │                              │    ◄── recall() ────────│
    │                              │        "Auth spec"      │
    │                              ├─── spec returned ──────►
    │                              │                         │
    │                              │    ◄── done() ─────────│
    │                              │        "verified"      │
    │                              ├─── task completed ────►
    │                              │                         │
    │ (Next session)               │                         │
    │                              │                         │
    │─── list_tasks() ────────────►│                         │
    │    (manual check)            │                         │
    │◄─── [task 123: done] ─────────                         │
```

**Manual Steps**:
1. Alice calls `create_task("Implement auth")`
2. Alice calls `remember("Auth spec", "...")`
3. Alice's session ends (code needs to await)
4. Bob manually calls `claim_task()` (what if another agent already did?)
5. Bob calls `recall("Auth spec")`
6. Bob calls `done(task, "verified")`
7. Charlie checks `list_tasks()` manually to see progress
8. Charlie has no visibility into what happened

**Problems**:
- ❌ 6 explicit Samvit calls for one workflow
- ❌ Double-assignment if Bob and Charlie both claim same task
- ❌ No guarantee task completes if Bob crashes mid-work
- ❌ Alice's session blocks until task creation returns
- ❌ Charlie has no visibility

---

## Proposed (Autonomous) Workflow

### Same scenario: Alice creates task → Bob claims and works → Charlie verifies

```
AUTONOMOUS (Decorator-Based)
═══════════════════════════════════════════════════════════════

Alice's Agent               Samvit Server              Bob's Agent
    │                            │                         │
    │ @samvit.task              │                         │
    │ async def implement():    │                         │
    │     remember(spec)        │                         │
    │     return "done"         │                         │
    │                           │                         │
    │──── call implement() ─────►│                         │
    │                           ├─ auto-create task       │
    │                           ├─ auto-claim task        │
    │                           ├─ auto-execute           │
    │                           ├─ auto-complete          │
    │◄─── "done" ────────────────                         │
    │                           │                         │
    │ (Alice's session ends)    │                         │
    │                           │                         │
    │                           │    ◄── call verify() ───│
    │                           │        (auto-claims     │
    │                           │         previous task)  │
    │                           ├─ task auto-claimed     │
    │                           ├─ auto-execute          │
    │                           ├─ auto-complete         │
    │                           ├─ "verified" ──────────►│
    │                           │                        │
    │ (Next session)            │                        │
    │ @samvit.task             │                        │
    │ async def deploy():       │                        │
    │     return "deployed"     │                        │
    │                           │                        │
    │──── call deploy() ────────►│                       │
    │                           ├─ task auto-claimed     │
    │                           ├─ auto-execute          │
    │                           ├─ auto-complete        │
    │◄─── "deployed" ────────────                       │
```

**Autonomous Steps**:
1. Alice calls `implement()` (just a function call)
   - Samvit auto-creates task
   - Samvit auto-claims task
   - Samvit auto-completes task
2. Bob calls `verify()` (same pattern)
   - Samvit auto-detects previous task dependency
   - Samvit auto-claims it
   - Samvit auto-completes it
3. Charlie calls `deploy()` (same pattern)
   - Same

**Benefits**:
- ✅ ZERO explicit Samvit calls in agent code
- ✅ Task claiming automatic (no double-assignment)
- ✅ Crash recovery automatic (retry + timeout handled)
- ✅ Alice's session doesn't block
- ✅ Full visibility (all tasks auto-logged)

---

## Side-by-Side: Lines of Code

### Manual (Current)

```python
# Alice's agent
task_id = await create_task("Implement auth", priority=1)  # Line 1
result = await implement_auth()                             # Line 2
await remember("Auth spec", "Use JWT")                     # Line 3
await done(task_id, result)                                # Line 4

# Bob's agent
task = await claim_task()                                  # Line 5
spec = await recall("Auth spec")                           # Line 6
result = verify_auth(spec)                                 # Line 7
await done(task, result)                                   # Line 8

# Charlie's agent
task = await claim_task()                                  # Line 9
await done(task, "deployed")                               # Line 10

# Total: 10 lines of Samvit boilerplate
```

### Autonomous (Proposed)

```python
# Alice's agent
@samvit.task
async def implement_auth():                                # Line 1
    await remember("Auth spec", "Use JWT")
    return "done"

# Bob's agent
@samvit.task
async def verify_auth():                                   # Line 2
    spec = await recall("Auth spec")
    return verify_logic(spec)

# Charlie's agent
@samvit.task
async def deploy():                                        # Line 3
    return "deployed"

# Total: 3 lines of Samvit boilerplate (the decorator)
```

**Reduction**: 10 lines → 3 decorators (70% less code)

---

## What Samvit Does Behind the Scenes (Autonomous)

When you call `@samvit.task` decorated function:

```
User calls:  await implement_auth()
                    │
                    ▼
Samvit intercepts:
    1. Create task with function signature
       → task_id = f"implement_auth_{uuid}"
    
    2. Lock task (atomic claiming)
       → SQL: FOR UPDATE SKIP LOCKED
    
    3. Execute function
       → result = await implement_auth()
    
    4. Handle failures
       → If crash: retry (max_retries times)
       → If timeout: release task (timeout seconds)
       → If success: mark done
    
    5. Log to audit trail
       → INSERT audit_log(task_id, status, result)
    
    6. Return result to caller
       → return result
```

All transparent. Engineer writes one decorator. Samvit does the rest.

---

## Failure Scenarios: Manual vs Autonomous

### Scenario: Agent crashes mid-task

**Manual (Current)**:
```
Task created: "implement_auth"
Alice's agent: claim_task() → got task
Alice's agent: execute... CRASH 💥
Task status: CLAIMED (stuck forever)
Bob: Can't claim it (already claimed, but owner is dead)
Result: DEADLOCK
```

**Autonomous (Proposed)**:
```
Task created: "implement_auth"
Samvit: claim_task() + set_timeout(300s)
Alice's agent: execute... CRASH 💥
Samvit: 30 seconds pass...
Samvit: timeout_checker.run() → task > 300s old?
Samvit: release_task(auto-retry-with-new-owner)
Bob: can now claim_task()
Result: AUTO-RECOVERY ✅
```

**Key Difference**: Timeout + auto-release handled by decorator, not manual code.

---

## Visibility Comparison

### Manual (Current)

Charlie wants to know: "What's the status?"

```bash
# Charlie must write:
tasks = await list_tasks()
for task in tasks:
    if task.status == "done":
        print(f"Task {task.id}: {task.status}")
```

Charlie sees:
```
Task 123: claimed (by Bob, 5 minutes ago)
Task 456: done (5 minutes ago)
Task 789: pending
```

Charlie doesn't know:
- Is task 123 actually running or stuck?
- When will task 789 start?
- What did each task do?

---

### Autonomous (Proposed)

Charlie wants to know: "What's the status?"

Samvit auto-provides:
```
Task implement_auth: done (2m ago)
  └─ Owner: alice_agent
  └─ Result: "done"
  └─ Retries: 0
  └─ Duration: 1m 30s

Task verify_auth: claimed (30s ago)
  └─ Owner: bob_agent
  └─ Started: 30s ago
  └─ Timeout: 270s remaining
  └─ Auto-retries-on-timeout: 3 remaining

Task deploy: pending (0s ago)
  └─ Will claim when verify_auth completes
```

Charlie sees:
- What each task did ✅
- How long it took ✅
- Who owns it ✅
- What happens if it times out ✅
- Auto-recovery status ✅

---

## Summary: Manual vs Autonomous

| Aspect | Manual | Autonomous |
|--------|--------|-----------|
| **Lines of code** | 10+ per agent | 1 decorator per agent |
| **Task creation** | Manual `create_task()` | Automatic from decorator |
| **Task claiming** | Manual `claim_task()` | Automatic on call |
| **Task completion** | Manual `done()` | Automatic on return |
| **Failure handling** | Manual try/except | Automatic retry + timeout |
| **Lease renewal** | Manual `renew_lease()` | Automatic background task |
| **Visibility** | Manual `list_tasks()` | Automatic full audit |
| **Double-assignment** | Possible (race condition) | Impossible (atomic lock) |
| **Learning curve** | Steep (understand all the calls) | Flat (just use decorator) |

