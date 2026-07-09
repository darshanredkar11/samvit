# Samvit Autonomous Runner — Technical Specification

**Branch**: `feat/autonomous-runner`  
**Status**: Ready for implementation  
**Model**: Spec authored by Claude Sonnet. Implementation delegated to Claude Haiku per task below.

---

## 1. Goal

Enable AI clients to coordinate autonomously — without humans mediating every exchange.

Today: Human A types → Claude Code calls `remember()` → Samvit → Kiro calls `recall()` → Human B reads.  
After this spec: Runner-A claims task, executes agentic loop, stores result → Runner-B discovers output and builds on it → humans review at their own pace.

Humans set direction and review output. Runners do the work in between.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Developer A                    Developer B                     │
│                                                                 │
│  Claude Code (interactive)      Kiro (interactive)              │
│  creates tasks, reviews output  reviews output, steers          │
│       │                              │                          │
│  samvit-runner (daemon)         samvit-runner (daemon)          │
│  tags: [auto:backend]           tags: [auto:frontend]           │
│       │                              │                          │
│       └──────────── SSE ─────────────┘                         │
│                     │                                           │
│              Samvit Server                                      │
│         task queue · memory · code graph                        │
│              msg bus · audit log                                │
│                     │                                           │
│              PostgreSQL 16 + pgvector                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 — SSE Event Stream (Samvit server change)

**New endpoint**: `GET /v1/events`

Authenticated (same bearer token). Returns `text/event-stream`.

Pushes events to connected runners so they react instantly — no polling.

#### Event types

```json
// New task available matching runner's tags
{"type": "task_available", "task_id": "uuid", "title": "...", "tags": ["auto:backend"]}

// Task was claimed (runners should skip it)
{"type": "task_claimed", "task_id": "uuid", "claimed_by": "runner-A"}

// Task completed — other runners may build on it
{"type": "task_done", "task_id": "uuid", "completed_by": "runner-A", "result_key": "task.uuid.result"}

// Message addressed to this agent
{"type": "message_received", "from": "runner-A", "body": "...", "topic": "..."}

// Heartbeat — keeps connection alive, client ignores
{"type": "ping"}
```

#### Server implementation

```python
# In samvit/main.py

from asyncio import Queue
from samvit.events import event_bus   # new module

@app.get("/v1/events")
async def event_stream(request: Request):
    agent = _agent()
    queue: Queue = await event_bus.subscribe(
        workspace_id=agent["workspace_id"],
        agent_id=str(agent["id"]),
    )
    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield "data: {\"type\":\"ping\"}\n\n"
        finally:
            await event_bus.unsubscribe(agent["workspace_id"], str(agent["id"]))
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

#### Event bus module: `samvit/events.py`

```python
# In-process pub/sub. Workspace-scoped. Agent-scoped queues.
# When create_task() is called → publishes task_available to all runners in workspace.
# When done() is called → publishes task_done to all agents in workspace.
# When say() is called → publishes message_received to the addressed agent.

class EventBus:
    def __init__(self):
        # workspace_id → {agent_id → Queue}
        self._subscribers: dict[str, dict[str, Queue]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, workspace_id: str, agent_id: str) -> Queue:
        async with self._lock:
            ws = self._subscribers.setdefault(workspace_id, {})
            q: Queue = asyncio.Queue(maxsize=200)
            ws[agent_id] = q
            return q

    async def unsubscribe(self, workspace_id: str, agent_id: str) -> None:
        async with self._lock:
            self._subscribers.get(workspace_id, {}).pop(agent_id, None)

    async def publish(self, workspace_id: str, event: dict,
                      target_agent_id: str | None = None) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(workspace_id, {})
            targets = (
                {target_agent_id: subscribers[target_agent_id]}
                if target_agent_id and target_agent_id in subscribers
                else subscribers
            )
            for q in targets.values():
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass   # slow consumer — drop event, runner reconnects

event_bus = EventBus()
```

#### Hook event_bus into existing tools

```python
# In tools/tasks.py — after INSERT in create():
await event_bus.publish(workspace_id, {
    "type": "task_available",
    "task_id": str(task_id),
    "title": title,
    "tags": task_tags,
})

# After UPDATE in done():
await event_bus.publish(workspace_id, {
    "type": "task_done",
    "task_id": task_id,
    "completed_by": agent["handle"],
    "result_key": f"task.{task_id}.result",
})

# In tools/messaging.py — after INSERT in say():
await event_bus.publish(workspace_id, {
    "type": "message_received",
    "from": agent["handle"],
    "body": body,
    "topic": topic,
}, target_agent_id=to_agent_id)
```

---

### 3.2 — Task Routing Tags (convention, no schema change)

Tags are already `text[]` on the tasks table. Two new conventions:

| Tag prefix | Consumer | Meaning |
|---|---|---|
| `auto:*` | samvit-runner daemons | Runner claims and executes autonomously |
| `auto:backend` | runners with backend tools | Python, DB, API work |
| `auto:frontend` | runners with frontend tools | JS/TS, CSS, React work |
| `auto:test` | runners with test tools | Writing/running tests only |
| `human:review` | human AI clients | Runner finished, human must review |
| `human:decide` | human AI clients | Runner is blocked, needs a decision |

Runners filter `claim(tags=["auto:backend"])`. Human clients filter `list_tasks(tags=["human:review"])`.

No database migration needed. Convention only.

---

### 3.3 — `request_review` MCP Tool (Samvit server change)

New tool added to `main.py`. Called by runners when they complete work.

```python
@mcp.tool(description=(
    "Signal that autonomous work is complete and ready for human review. "
    "Creates a human:review task visible to the original task creator. "
    "Include a clear summary of what was done and what the human should check."
))
async def request_review(
    task_id:       str,
    summary:       str,
    files_changed: list[str] | None = None,
    ctx: Context = None,
) -> dict:
    agent = _agent()
    try:
        return await tasks.request_review(agent, task_id, summary, files_changed or [])
    except (LookupError, ValueError) as exc:
        return _mcp_error(400, str(exc))
    except Exception:
        return _mcp_error(500, "Internal error")
```

Implementation in `tools/tasks.py`:

```python
async def request_review(
    agent: dict,
    task_id: str,
    summary: str,
    files_changed: list[str],
) -> dict:
    tid = _task_uuid(task_id)
    async with db.pool().acquire() as conn:
        original = await conn.fetchrow(
            "SELECT title, created_by FROM tasks WHERE id = $1 AND workspace_id = $2",
            tid, agent["workspace_id"],
        )
        if not original:
            raise LookupError(f"Task {task_id} not found")

        # Store structured result in memory
        await conn.execute(
            """
            INSERT INTO semantic_memory (agent_id, namespace, content, workspace_id)
            VALUES ($1, 'global', $2, $3)
            """,
            agent["id"],
            f"Task result [{original['title']}]: {summary}",
            agent["workspace_id"],
        )

        # Create human:review task for the original creator
        review_id = await conn.fetchval(
            """
            INSERT INTO tasks (title, description, tags, created_by, workspace_id)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            f"Review: {original['title']}",
            f"{summary}\n\nFiles changed:\n" + "\n".join(f"  - {f}" for f in files_changed),
            ["human:review"],
            agent["id"],
            agent["workspace_id"],
        )

    return {"review_task_id": str(review_id), "created": True}
```

---

### 3.4 — `samvit-runner` Package (new package)

A standalone pip-installable daemon. Lives in `runner/` directory in this repo.

```
relay/
├── samvit/           ← existing server
├── runner/           ← NEW
│   ├── pyproject.toml
│   ├── README.md
│   └── samvit_runner/
│       ├── __init__.py
│       ├── __main__.py       ← entry point: python -m samvit_runner
│       ├── cli.py            ← argparse: --url, --token, --tags, --max-steps
│       ├── client.py         ← Samvit HTTP + SSE client
│       ├── loop.py           ← agentic loop (Anthropic tool_use API)
│       ├── tools.py          ← coding tools: read_file, write_file, run_command, run_tests
│       ├── stuck.py          ← stuck detection: same file written N times = stop
│       └── review.py         ← request_review + notify human flow
```

#### CLI

```bash
pip install samvit-runner

samvit-runner \
  --url   http://your-samvit-server:8765 \
  --token samvit_xxx \
  --tags  auto:backend \
  --workspace /path/to/your/repo \
  --max-steps 20 \
  --model claude-haiku-4-5   # default; override with any Anthropic model
```

#### `client.py` — Samvit HTTP + SSE client

```python
class SamvitRunnerClient:
    """Thin HTTP client + SSE subscriber for samvit-runner."""

    def __init__(self, url: str, token: str):
        self.base = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}

    async def claim(self, tags: list[str]) -> dict | None:
        r = await self._post("/v1/tools/call", {
            "tool": "claim", "params": {"tags": tags}
        })
        return r.get("task")

    async def done(self, task_id: str, claim_token: str, result: dict) -> None:
        await self._post("/v1/tools/call", {
            "tool": "done",
            "params": {"task_id": task_id, "claim_token": claim_token, "result": result}
        })

    async def remember(self, content: str, key: str | None = None) -> None:
        await self._post("/v1/tools/call", {
            "tool": "remember",
            "params": {"content": content, "key": key, "namespace": "global"}
        })

    async def recall(self, query: str, limit: int = 5) -> list[dict]:
        r = await self._post("/v1/tools/call", {
            "tool": "recall", "params": {"query": query, "limit": limit}
        })
        return r.get("results", [])

    async def request_review(self, task_id: str, summary: str, files: list[str]) -> None:
        await self._post("/v1/tools/call", {
            "tool": "request_review",
            "params": {"task_id": task_id, "summary": summary, "files_changed": files}
        })

    async def say(self, to: str, body: str) -> None:
        await self._post("/v1/tools/call", {
            "tool": "say", "params": {"to": to, "body": body}
        })

    async def event_stream(self, tags: list[str]):
        """Async generator: yields events from /v1/events SSE stream."""
        async with httpx.AsyncClient(headers=self.headers, timeout=None) as c:
            async with c.stream("GET", f"{self.base}/v1/events") as r:
                async for line in r.aiter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        if event["type"] == "ping":
                            continue
                        if event["type"] == "task_available":
                            task_tags = set(event.get("tags", []))
                            if task_tags & set(tags):
                                yield event
                        else:
                            yield event

    async def _post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient(headers=self.headers, timeout=30) as c:
            r = await c.post(f"{self.base}{path}", json=body)
            r.raise_for_status()
            return r.json()
```

#### `loop.py` — Agentic loop

```python
import anthropic

SYSTEM_PROMPT = """
You are an autonomous software engineering agent connected to a multi-agent coordination system.

You have been assigned a task. Complete it using the tools available.
Rules:
- Write clean, tested code
- Run tests after every significant change
- Stop and call request_review when done — never push or deploy
- If you are stuck (same error 3 times), call request_human_decision
- Prefer small, focused changes over large rewrites
"""

async def agent_loop(
    task: dict,
    memory: list[dict],
    client: SamvitRunnerClient,
    workspace: str,
    max_steps: int = 20,
    model: str = "claude-haiku-4-5",
) -> LoopResult:
    llm = anthropic.Anthropic()
    tools = build_tool_definitions(workspace)
    stuck = StuckDetector(threshold=3)

    context = build_initial_context(task, memory)
    messages = [{"role": "user", "content": context}]
    files_changed: list[str] = []
    steps = 0

    while steps < max_steps:
        steps += 1
        response = llm.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        # Accumulate assistant turn
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Model finished — extract summary from final text block
            summary = next(
                (b.text for b in response.content if hasattr(b, "text")), ""
            )
            return LoopResult(summary=summary, files_changed=files_changed, success=True)

        if response.stop_reason != "tool_use":
            break

        # Execute tool calls
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            result = await execute_tool(block.name, block.input, workspace)

            # Track changed files for review
            if block.name == "write_file":
                path = block.input.get("path", "")
                if path not in files_changed:
                    files_changed.append(path)
                stuck.record_write(path)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})

        # Stuck detection
        if stuck.is_stuck():
            return LoopResult(
                summary=f"Stuck after {steps} steps. Repeated writes to {stuck.stuck_path()} with no progress.",
                files_changed=files_changed,
                success=False,
                needs_human=True,
            )

    return LoopResult(
        summary=f"Reached step limit ({max_steps}) without completing.",
        files_changed=files_changed,
        success=False,
        needs_human=True,
    )
```

#### `tools.py` — Coding tools

```python
TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path from workspace root"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates the file and parent directories if needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "run_command",
        "description": "Run a shell command in the workspace. Use for git status, linting, building.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "default": 30}
            },
            "required": ["command"]
        }
    },
    {
        "name": "run_tests",
        "description": "Run the test suite. Returns pass/fail counts and failure details.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Test file or directory. Empty = all tests."},
                "timeout": {"type": "integer", "default": 60}
            }
        }
    },
    {
        "name": "list_files",
        "description": "List files in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."}
            }
        }
    },
    {
        "name": "request_review",
        "description": "Signal that work is complete. Notify the human for review. ALWAYS call this when done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary":       {"type": "string", "description": "What was done and what the human should check."},
                "files_changed": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["summary"]
        }
    },
    {
        "name": "request_human_decision",
        "description": "You are blocked and need a human decision. Describe exactly what you need.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "context":  {"type": "string"}
            },
            "required": ["question"]
        }
    },
]

async def execute_tool(name: str, inputs: dict, workspace: str) -> str:
    root = Path(workspace).resolve()

    match name:
        case "read_file":
            p = (root / inputs["path"]).resolve()
            _assert_within(p, root)
            return p.read_text(errors="replace") if p.exists() else f"File not found: {inputs['path']}"

        case "write_file":
            p = (root / inputs["path"]).resolve()
            _assert_within(p, root)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(inputs["content"])
            return f"Written: {inputs['path']} ({len(inputs['content'])} chars)"

        case "run_command":
            proc = await asyncio.create_subprocess_shell(
                inputs["command"],
                cwd=root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=inputs.get("timeout", 30))
            except asyncio.TimeoutError:
                proc.kill()
                return "Command timed out"
            return out.decode(errors="replace")[:4000]   # cap output

        case "run_tests":
            cmd = f"python -m pytest {inputs.get('path', '')} -q --tb=short 2>&1"
            proc = await asyncio.create_subprocess_shell(cmd, cwd=root,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(),
                    timeout=inputs.get("timeout", 60))
            except asyncio.TimeoutError:
                proc.kill()
                return "Tests timed out"
            return out.decode(errors="replace")[:4000]

        case "list_files":
            p = (root / inputs.get("path", ".")).resolve()
            _assert_within(p, root)
            return "\n".join(str(f.relative_to(root)) for f in sorted(p.rglob("*"))[:200])

        case _:
            return f"Unknown tool: {name}"

def _assert_within(path: Path, root: Path):
    if not str(path).startswith(str(root)):
        raise PermissionError(f"Path escape blocked: {path}")
```

#### `stuck.py` — Stuck detection

```python
from collections import Counter

class StuckDetector:
    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._writes: Counter = Counter()

    def record_write(self, path: str) -> None:
        self._writes[path] += 1

    def is_stuck(self) -> bool:
        return any(count >= self.threshold for count in self._writes.values())

    def stuck_path(self) -> str:
        return max(self._writes, key=self._writes.get, default="unknown")
```

#### `__main__.py` — Main daemon loop

```python
async def main(args):
    client = SamvitRunnerClient(args.url, args.token)
    tags   = args.tags.split(",")
    print(f"samvit-runner starting — tags: {tags}, workspace: {args.workspace}")

    # Register with Samvit (use existing agent token — no new registration needed)
    # SSE stream handles reconnection automatically

    reconnect_delay = 1

    while True:
        try:
            async for event in client.event_stream(tags):
                reconnect_delay = 1  # reset on successful connection

                if event["type"] != "task_available":
                    continue

                task = await client.claim(tags=tags)
                if not task:
                    continue  # race — another runner got it

                print(f"→ Claimed: {task['title']}")

                # Pull relevant memory
                memory = await client.recall(task["title"])

                # Run agentic loop
                result = await agent_loop(
                    task      = task,
                    memory    = memory,
                    client    = client,
                    workspace = args.workspace,
                    max_steps = args.max_steps,
                    model     = args.model,
                )

                if result.success:
                    # Store summary as shared memory
                    await client.remember(
                        f"Completed [{task['title']}]: {result.summary}",
                        key=f"task.{task['id']}.result"
                    )
                    # Request human review
                    await client.request_review(
                        task_id = task["id"],
                        summary = result.summary,
                        files   = result.files_changed,
                    )
                    await client.done(task["id"], task["claim_token"],
                                      result={"summary": result.summary, "files": result.files_changed})
                    print(f"✓ Done: {task['title']}")
                else:
                    # Stuck or step-limited — ask human
                    await client.say(
                        to   = task.get("created_by", ""),
                        body = f"Stuck on: {task['title']}\n\n{result.summary}",
                    )
                    # Return task to pending so a human can redirect it
                    await client.done(task["id"], task["claim_token"],
                                      result={"error": result.summary}, status="failed")
                    print(f"✗ Stuck: {task['title']}")

        except httpx.HTTPError as exc:
            print(f"Connection error: {exc} — retrying in {reconnect_delay}s")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
        except KeyboardInterrupt:
            print("samvit-runner stopped")
            break
```

---

## 4. Data Flow — Full Example

```
1. Human A (Claude Code):
   "Create a task tagged auto:backend: Add rate limiting to POST /v1/tasks"
   → create_task("Add rate limiting to POST /v1/tasks", tags=["auto:backend"])
   → Samvit publishes SSE: task_available

2. runner-A (daemon, no human prompt):
   ← SSE: task_available {tags: ["auto:backend"]}
   → claim()                         # atomic, FOR UPDATE SKIP LOCKED
   → recall("rate limiting")         # gets any prior decisions from Samvit memory
   → agent_loop():
       read_file("samvit/main.py")
       read_file("samvit/ratelimit.py")
       write_file("samvit/ratelimit.py", ...)    # improved rate limiter
       write_file("samvit/main.py", ...)         # wire it in
       run_tests()                               # pytest passes
       request_review(summary="Added per-endpoint rate limiting...", files=[...])
   → done(task_id, result={...})
   → Samvit publishes SSE: task_done

3. Human A (Claude Code), later:
   "What needs review?"
   → list_tasks(tags=["human:review"])
   → sees: "Review: Add rate limiting to POST /v1/tasks"
   → recall("task.xyz.result")
   → reads summary, checks files
   "Looks good. Create a task to write integration tests for this, auto:test"

4. runner-B (daemon, Human B's machine, tags=[auto:test]):
   ← SSE: task_available
   → claim()
   → recall("rate limiting")                     # gets runner-A's result automatically
   → agent_loop():
       read_file("samvit/ratelimit.py")          # reads what runner-A wrote
       write_file("tests/test_ratelimit_integration.py", ...)
       run_tests("tests/test_ratelimit_integration.py")
       request_review(summary="12 integration tests written, all passing")
   → done()

5. Human B (Kiro):
   "What's ready for review?"
   → list_tasks(tags=["human:review"])
   → reviews test file, merges
```

---

## 5. Implementation Tasks (Haiku-delegatable)

Each task is self-contained with clear inputs, outputs, and acceptance criteria.

---

### TASK-1: Create `samvit/events.py` — EventBus

**Input**: The `EventBus` class spec in section 3.1  
**Output**: `samvit/events.py` — complete, tested  
**Acceptance**:
- `subscribe(workspace_id, agent_id)` returns an `asyncio.Queue`
- `unsubscribe(workspace_id, agent_id)` removes the queue
- `publish(workspace_id, event)` delivers to all subscribers in that workspace
- `publish(workspace_id, event, target_agent_id)` delivers to one subscriber only
- `QueueFull` is silently dropped (slow consumer)
- Tests cover: subscribe, publish-all, publish-one, unsubscribe, queue-full

---

### TASK-2: Add SSE endpoint `GET /v1/events` to `main.py`

**Input**: Endpoint spec in section 3.1  
**Depends on**: TASK-1  
**Output**: New route in `samvit/main.py` + import of `event_bus`  
**Acceptance**:
- Returns `text/event-stream` with `Cache-Control: no-cache`
- Requires bearer token auth (uses existing `_agent()`)
- Sends `{"type":"ping"}` every 30s of inactivity
- Unsubscribes cleanly on client disconnect
- Does NOT block other requests (async generator)

---

### TASK-3: Hook `event_bus.publish()` into `tasks.py` and `messaging.py`

**Input**: Hook spec in section 3.1  
**Depends on**: TASK-1  
**Output**: Modified `tools/tasks.py` and `tools/messaging.py`  
**Acceptance**:
- `create()` publishes `task_available` after INSERT
- `done()` publishes `task_done` after UPDATE
- `say()` publishes `message_received` with `target_agent_id` of the recipient
- Publishing failure does NOT raise — fire and forget

---

### TASK-4: Add `request_review` tool to `tools/tasks.py` and `main.py`

**Input**: `request_review` spec in section 3.3  
**Output**: `tools/tasks.py` function + MCP tool decorator in `main.py`  
**Acceptance**:
- Raises `LookupError` if task_id not found or wrong workspace
- Stores summary in `semantic_memory` with `namespace='global'`
- Creates a `human:review` task for the original creator
- Returns `{"review_task_id": "uuid", "created": true}`
- Test: creates review task, memory entry visible to recall

---

### TASK-5: Create `runner/` package scaffold

**Input**: Package structure in section 3.4  
**Output**: `runner/pyproject.toml`, `runner/samvit_runner/__init__.py`, `runner/samvit_runner/__main__.py`, `runner/README.md`  
**Acceptance**:
- `pip install -e runner/` succeeds
- `python -m samvit_runner --help` prints usage
- `pyproject.toml` has entry point `samvit-runner = samvit_runner.cli:main`
- Dependencies: `httpx>=0.27`, `anthropic>=0.40`, `asyncio` (stdlib)

---

### TASK-6: Implement `runner/samvit_runner/client.py`

**Input**: `SamvitRunnerClient` spec in section 3.4  
**Output**: Complete `client.py`  
**Acceptance**:
- All methods shown in spec implemented
- `event_stream()` is an async generator that reconnects on `httpx.ReadTimeout`
- `event_stream(tags)` filters `task_available` events to only matching tags
- `_post()` raises `httpx.HTTPStatusError` on non-2xx

---

### TASK-7: Implement `runner/samvit_runner/tools.py`

**Input**: Tool definitions and `execute_tool()` in section 3.4  
**Output**: Complete `tools.py` with `TOOL_DEFINITIONS` list and `execute_tool()` coroutine  
**Acceptance**:
- `read_file`: reads file, returns content, returns "File not found" if missing
- `write_file`: creates parent dirs, writes file, returns confirmation
- `run_command`: async subprocess, caps output at 4000 chars, timeout kills process
- `run_tests`: runs pytest, returns output, caps at 4000 chars
- `list_files`: recursive, returns up to 200 paths
- `_assert_within()`: blocks path traversal outside workspace root — raises `PermissionError`

---

### TASK-8: Implement `runner/samvit_runner/stuck.py` + `runner/samvit_runner/loop.py`

**Input**: `StuckDetector` and `agent_loop()` specs in section 3.4  
**Output**: `stuck.py` and `loop.py`  
**Acceptance**:
- `StuckDetector.is_stuck()` returns True after `threshold` writes to same path
- `agent_loop()` stops at `max_steps` and returns `LoopResult(success=False, needs_human=True)`
- `agent_loop()` returns on `stop_reason == "end_turn"` with `success=True`
- Tool calls are dispatched to `execute_tool()`
- `files_changed` list is accumulated across all steps, no duplicates

---

### TASK-9: Implement `runner/samvit_runner/__main__.py` — daemon loop

**Input**: `main()` spec in section 3.4  
**Output**: Complete `__main__.py`  
**Acceptance**:
- Connects to `event_stream`, filters by tags
- Claims task on `task_available`, skips if claim returns None
- Runs `agent_loop`, then calls `request_review` + `done` on success
- Calls `say` + marks task `failed` on stuck/step-limit
- Exponential backoff on connection errors (1s → 2s → 4s → ... → 60s cap)
- `KeyboardInterrupt` exits cleanly

---

### TASK-10: Tests for SSE endpoint + event bus

**Input**: TASK-1, TASK-2, TASK-3  
**Output**: `tests/test_events.py`  
**Acceptance**:
- Test: publish after `create_task` → subscriber receives `task_available`
- Test: publish after `done` → subscriber receives `task_done`
- Test: publish after `say` → only addressed agent receives `message_received`
- Test: SSE endpoint returns 401 without token
- Test: SSE endpoint sends ping on 30s timeout (use `asyncio.wait_for` with short timeout)
- Test: unsubscribed agent does not receive events

---

## 6. Dependency Order

```
TASK-1 (events.py)
  ├── TASK-2 (SSE endpoint)
  └── TASK-3 (hooks into tasks/messaging)
        └── TASK-4 (request_review)
                └── TASK-10 (tests)

TASK-5 (runner scaffold)
  ├── TASK-6 (client.py)
  ├── TASK-7 (tools.py)
  └── TASK-8 (loop.py + stuck.py)
        └── TASK-9 (daemon main)
```

TASK-1 → TASK-2, TASK-3, TASK-4 can run in parallel after TASK-1.  
TASK-5 → TASK-6, TASK-7, TASK-8 can run in parallel after TASK-5.  
TASK-9 depends on TASK-6 + TASK-7 + TASK-8.  
TASK-10 depends on TASK-1 + TASK-2 + TASK-3.

---

## 7. What Changes in `pyproject.toml`

`samvit` (server) package: add `sse-starlette` or use `starlette.responses.StreamingResponse` (already available via FastAPI — no new dep).

`samvit-runner` (new package): separate `runner/pyproject.toml`, not a workspace member of the server. Install separately.

---

## 8. Acceptance for the Full Feature

Manual integration test:

```bash
# Terminal 1: Samvit server
docker compose up -d
samvit register darshan --provider claude-code --url http://localhost:8765
# copy token

# Terminal 2: Runner (autonomous daemon)
samvit-runner \
  --url http://localhost:8765 \
  --token samvit_xxx \
  --tags auto:backend \
  --workspace /path/to/test/repo \
  --max-steps 5

# Terminal 3: Create a task
curl -X POST http://localhost:8765/v1/tasks \
  -H "Authorization: Bearer samvit_xxx" \
  -d '{"title":"Add a hello.py that prints hello world","tags":["auto:backend"]}'

# Expected:
# Runner terminal: "→ Claimed: Add a hello.py that prints hello world"
# Runner terminal: "✓ Done: Add a hello.py that prints hello world"
# test/repo/hello.py exists
# A human:review task exists in the queue
```
