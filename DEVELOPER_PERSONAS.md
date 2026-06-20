# Samvit Developer Personas

## Who Actually Uses Samvit?

Not "all developers." Specific types with specific pain points.

---

## Persona A: The Multi-Agent Team Lead

**Name**: Alex (Backend engineer, 5 years experience)

**Situation**:
- Leading a team of 3 people
- Each uses Claude Code or Cursor
- Need to coordinate work: "Alice fixes auth, Bob verifies it, Charlie deploys"
- Currently: Manual Slack coordination, lost context across sessions

**Goals**:
- Agents remember decisions without re-explaining
- Tasks don't get duplicated (atomic assignment)
- Visibility into who did what (audit trail)

**Pain Points**:
- "I have to explain the auth requirements to Alice every session"
- "Bob and Charlie both claimed the same verification task"
- "No way to know what happened if someone's laptop crashes"

**When they reach for Samvit**:
- Multiple agents need to coordinate
- Tasks should be assigned to exactly one agent
- Decisions need to persist across sessions/days

**Current Workflow**:
```python
# Alice's agent (Claude Code)
task_id = await create_task("Implement auth", priority=1)
remembered = await remember("Auth spec", "Use JWT with RS256")

# Bob's agent (Cursor)
task = await claim_task()  # Hope it's the right one
spec = await recall("Auth spec")
result = await done(task, "verified")

# Charlie's agent (Antigravity)
# Manual: "What tasks are done? Let me check Slack..."
```

**Desired Workflow**:
```python
# Alice's agent
@samvit.task
async def implement_auth():
    await remember("Auth spec", "Use JWT with RS256")
    return "implemented"

# Bob's agent
@samvit.task
async def verify_auth():
    spec = await recall("Auth spec")
    return "verified"

# Charlie's agent
@samvit.task
async def deploy_auth():
    return "deployed"

# Samvit auto-handles: ordering, retries, visibility
```

**Success Metric**: "We use Samvit without thinking about it"

---

## Persona B: The Solo Rapid Builder

**Name**: Jordan (AI/ML engineer, 2 years experience)

**Situation**:
- Building a rapid prototype (research project)
- Using 5+ agents simultaneously (Claude Code + Cursor + custom script)
- Agents keep failing, losing work, re-doing tasks

**Goals**:
- Agents survive crashes (persistent state)
- Don't repeat work (atomic task queue)
- Fast iteration (low setup overhead)

**Pain Points**:
- "Agent crashed and now the same task is queued twice"
- "I spent 30 min setting up task coordination, still doesn't work"
- "How do I know if agent 3 actually processed agent 1's output?"

**When they reach for Samvit**:
- Need to coordinate more than 2 agents
- Can't afford to lose work to crashes
- Setup time is a blocker (they move fast)

**Current Workflow** (manual script):
```python
# Fragile, doesn't survive crashes
task_queue = []

async def agent_1():
    task_queue.append("process_data")

async def agent_2():
    if "process_data" in task_queue:
        task_queue.remove("process_data")
        # ... work ...
```

**Desired Workflow**:
```python
@samvit.task(max_retries=3, timeout=300)
async def process_data():
    return "processed"

@samvit.task(max_retries=3, timeout=300)
async def verify_processed():
    return "verified"

# Just works. Crashes are handled.
```

**Success Metric**: "Setup took 2 minutes, crashed agents auto-recover"

---

## Persona C: The Enterprise Integration Lead

**Name**: Sam (Platform engineer, 10 years experience)

**Situation**:
- Company has 10+ teams using Claude Code
- Teams scattered across projects
- Need cross-team coordination (Team A's output → Team B's work)
- Compliance requirements (audit trail mandatory)

**Goals**:
- Workspace isolation (Team A can't see Team B's tasks)
- Complete audit trail (who did what, when)
- Reliable coordination (no lost messages)

**Pain Points**:
- "We have no visibility into what agents are doing"
- "No way to prove Team A didn't see Team B's data (compliance)"
- "Agents occasionally miss tasks due to timing issues"

**When they reach for Samvit**:
- Enterprise-scale multi-team coordination
- Compliance + audit requirements
- Production reliability (atomic guarantees matter)

**Current Workflow**:
- Custom in-house solution (expensive to maintain)
- Or: CrewAI per-team (no cross-team coordination)

**Desired Workflow**:
```python
# Samvit auto-isolates workspaces, auto-audits
@samvit.task
async def team_a_process():
    return "result"

@samvit.task  
async def team_b_process():
    # Workspace isolation: Team B sees only their tasks
    # Audit trail: Samvit logs all mutations
    return "verified"
```

**Success Metric**: "Compliance audit takes 5 minutes (full history, no blind spots)"

---

## Persona D: The "I Don't Know What Samvit Is" Developer

**Name**: Casey (Frontend engineer, 4 years experience)

**Situation**:
- New to Samvit (saw it on GitHub)
- Building first multi-agent project
- Has 2 agents, no coordination yet

**Goals**:
- "I just want my agents to work together"
- Don't want to learn distributed systems
- Want copy-paste solution

**Pain Points**:
- "What even IS a workspace?"
- "Do I need this? Or is CrewAI enough?"
- "Setup docs assume I know PostgreSQL"

**When they reach for Samvit**:
- After trying CrewAI and realizing it's single-machine only
- After agents lose work to crashes
- After manual task coordination breaks

**Current Workflow**:
```python
# Just calling agents manually, hoping it works
result1 = await agent_a()
result2 = await agent_b(result1)
# What if agent_a crashes?
# What if this runs twice?
```

**Desired Workflow**:
```python
@samvit.task
async def agent_a():
    return "result"

@samvit.task
async def agent_b():
    return "verified"

# Just decorate, everything works.
# No learning curve.
```

**Success Metric**: "I copied 2 lines of code, now it's reliable"

---

## Cross-Persona Needs

All 4 personas need:

| Need | Persona A | Persona B | Persona C | Persona D |
|------|-----------|-----------|-----------|-----------|
| **Minimal setup** | ✅ | ✅✅ | ✅ | ✅✅ |
| **Auto-failure handling** | ✅ | ✅✅ | ✅ | ✅ |
| **Audit trail** | ✅ | - | ✅✅ | - |
| **Task isolation** | ✅ | ✅ | ✅✅ | - |
| **Low learning curve** | ✅ | ✅ | - | ✅✅ |

---

## The Common Thread

All 4 personas would benefit most from:

**ONE THING**: `@samvit.task` decorator that auto-handles:
- Task creation
- Task claiming
- Retry + timeout
- Completion
- Failure recovery

Everything else is secondary.

---

## Who Are We NOT Building For?

- Agents on a single machine (use CrewAI)
- Agents that don't need coordination (don't use Samvit)
- Teams unwilling to self-host (wait for ECC Pro equivalent)
- Projects with <2 agents (over-engineered)

---

## Primary User: Persona A (The Team Lead)

**Why**: 
- Most common real-world scenario
- Highest willingness to pay
- Clearest pain point (manual coordination)
- Best testimonial value ("team productivity 3x")

**Secondary User**: Persona B (The Rapid Builder)

**Why**:
- Early adopters (drive GitHub stars)
- Fast feedback (iterate quickly)
- Convert to Persona A customers later (as teams grow)

---

## Adoption Strategy (For Our Personas)

**Week 1**: Ship to Persona B (rapid builders)
- Fast setup, auto-recovery, works in 5 minutes
- They forgive docs if it just works

**Week 2**: Ship to Persona A (team leads)
- Enterprise features (workspace, audit)
- Better docs, admin UI

**Week 3**: Ship to Persona C (enterprise)
- Compliance features, workspace management
- Sales conversations

**Month 2**: Persona D naturally discovers via word-of-mouth
- "Hey, my team is doing this... you should try Samvit"

---

## Success Metrics (Per Persona)

**Persona A**: "Zero manual task coordination by month 2"
**Persona B**: "Setup in <5 minutes, auto-recovery works"
**Persona C**: "Full audit trail, zero compliance questions"
**Persona D**: "Copy-paste decorator, just works"

---

## What This Means for @samvit.task

For **Persona A**:
```python
@samvit.task
async def process_order():
    """Atomic, persisted, audited."""
    return "processed"
```

For **Persona B**:
```python
@samvit.task(max_retries=3, timeout=300)
async def process_data():
    """Crashes are handled, no babysitting."""
    return "processed"
```

For **Persona C**:
```python
@samvit.task(max_retries=3, timeout=300)
async def process_order():
    """Auto-audited, workspace-isolated."""
    return "processed"
```

For **Persona D**:
```python
@samvit.task
async def process_order():
    """Just works."""
    return "processed"
```

All use the same syntax.
All get the same reliability.
Only difference: configuration (advanced users tune, basic users don't).

