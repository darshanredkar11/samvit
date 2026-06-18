# Samvit Usage Guide

This guide explains Samvit for a regular Claude Code or Antigravity user. You do
not need to build an AI agent or write Python. You need one machine that can run
Docker and a network connection between your teammates' machines.

## What Samvit Changes

Without Samvit, Claude Code on your laptop and Antigravity on your teammate's
laptop are separate. They cannot see each other's notes, task ownership, or
messages.

With Samvit, both tools connect to one shared server:

```mermaid
flowchart LR
    U1["Developer A<br/>Claude Code<br/>Machine A"] -->|"MCP over private HTTP<br/>token: samvit_A"| S["Samvit Server<br/>port 8765"]
    U2["Developer B<br/>Antigravity<br/>Machine B"] -->|"MCP over private HTTP<br/>token: samvit_B"| S
    S --> P["PostgreSQL + pgvector<br/>memory, tasks, messages"]
    S --> R["Redpanda<br/>optional event delivery"]
```

Claude Code and Antigravity never connect directly to each other. They both call
Samvit tools. Samvit authenticates the caller, updates the shared database, and
returns the result.

For example:

1. Claude Code calls `remember` with an architecture decision.
2. Samvit scans the text for credentials and stores a local vector embedding.
3. Antigravity later calls `recall` with a related question.
4. Samvit searches the same shared namespace and returns Claude's note.

The same pattern applies to tasks and messages.

## Before You Start

Choose one machine to host Samvit. It can be:

- A developer laptop that stays online during team work
- A small office server
- A private VM
- A machine reachable through Tailscale, WireGuard, or another VPN

Do not expose port 8765 directly to the public internet. For a team, the simplest
safe setup is a private VPN. Every teammate should be able to open:

```text
http://SAMVIT_HOST:8765/health
```

Replace `SAMVIT_HOST` with the private IP or private DNS name of the server.

## Part 1: Start the Shared Server

Run these commands on the server machine:

```bash
git clone https://github.com/darshanredkar11/samvit.git
cd samvit
cp .env.example .env
```

Open `.env` and replace the default passwords:

```dotenv
POSTGRES_PASSWORD=use-a-long-random-password
SAMVIT_ADMIN_SECRET=use-another-long-random-secret
SAMVIT_BIND_ADDRESS=0.0.0.0
```

Use `0.0.0.0` only when the machine is protected by a firewall or private VPN.

Start Samvit:

```bash
docker compose up -d
```

Check it:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/ready
```

Expected results include `"status":"ok"` and `"status":"ready"`.

## Part 2: Register One Identity Per Teammate

Each person or long-running agent should receive a different token. Do not share
one token across the whole team because messages and task ownership use the
registered identity.

On the Samvit server, register the Claude Code user:

```bash
docker compose exec samvit samvit register darshan \
  --provider claude-code \
  --url http://127.0.0.1:8765
```

Register the Antigravity user:

```bash
docker compose exec samvit samvit register rahul \
  --provider antigravity \
  --url http://127.0.0.1:8765
```

Each command prints a different bearer token:

```json
{
  "agent_id": "a UUID",
  "token": "samvit_a_private_value"
}
```

Send each token only to its owner through a secure channel.

## Part 3: Connect Claude Code on Machine A

On the Claude Code machine, run:

```bash
claude mcp add --transport http samvit \
  http://SAMVIT_HOST:8765/mcp \
  --header "Authorization: Bearer CLAUDE_USERS_TOKEN"
```

Then verify the connection:

```bash
claude mcp list
```

Inside Claude Code, run `/mcp`. Samvit should appear as connected and should
show tools such as `remember`, `recall`, `create_task`, `claim`, `done`, `say`,
and `read`.

You can now speak normally:

```text
Remember globally that checkout webhooks are verified in payments/webhooks.py.
```

Claude should choose `remember`.

```text
Create a high-priority task for the backend team to add webhook replay tests.
```

Claude should choose `create_task`.

```text
Send Rahul a message saying the webhook design note is available.
```

Claude should choose `say`.

## Part 4: Connect Antigravity on Machine B

Antigravity versions may expose MCP configuration through a settings screen or
a JSON file. Add a remote MCP server with these values:

```json
{
  "name": "samvit",
  "transport": "http",
  "url": "http://SAMVIT_HOST:8765/mcp",
  "headers": {
    "Authorization": "Bearer ANTIGRAVITY_USERS_TOKEN"
  }
}
```

If your Antigravity version only offers legacy SSE, use:

```json
{
  "name": "samvit",
  "transport": "sse",
  "url": "http://SAMVIT_HOST:8765/legacy/sse",
  "headers": {
    "Authorization": "Bearer ANTIGRAVITY_USERS_TOKEN"
  }
}
```

Restart or reload Antigravity's MCP connections. Confirm that the Samvit tools
are visible. The exact menu name can vary by Antigravity release, but the server
URL, transport, and authorization header remain the same.

Now ask:

```text
Read my unread Samvit messages.
```

Rahul should receive the message sent by Claude Code on Machine A.

Then ask:

```text
Recall the team's notes about checkout webhooks.
```

Antigravity should receive the memory written by Claude Code.

## A Complete Two-Machine Test

Use this short test before trusting the setup for real work.

### On Machine A in Claude Code

```text
Use Samvit to remember globally:
"Team test: the release branch is release/2026-06."

Create a task tagged docs:
"Write the release checklist."

Send Rahul this message:
"Samvit connection test from Claude is complete."
```

### On Machine B in Antigravity

```text
Read my unread Samvit messages.

Recall global memories about the release branch.

Claim the next task tagged docs.
```

### Back on Machine A

```text
List the claimed tasks.
```

You should see the release-checklist task owned by Rahul. This proves:

- Both machines reach the same Samvit server
- Each machine has a distinct identity
- Shared memory works
- Directed messaging works
- Atomic task ownership works

## How the Main Tools Fit Together

### Shared memory

Use `remember` for decisions that another session or teammate will need.

```text
Remember globally that production uses PostgreSQL 16 and migrations run at startup.
```

Use `recall` to search by meaning:

```text
Recall what the team decided about database migrations.
```

Without `global`, memory defaults to the caller's private namespace.

### Task coordination

Use `create_task` to add work:

```text
Create a task tagged backend with priority 5:
"Add idempotency to the payment callback."
```

Use `claim` before beginning work. Samvit locks the task so another agent cannot
claim it at the same time.

Use `renew` during work that may exceed 30 minutes. Use `done` with the claim
token when complete. The AI client normally passes the token automatically.

### Team messaging

Use directed messages for a specific person:

```text
Send Rehma: "The API contract changed; read memory key api.checkout.v2."
```

Use topics for broadcasts:

```text
Broadcast to topic deploy: "Version 0.2 is running in staging."
```

Messages persist even when the recipient's AI tool is offline.

## How Code Indexing Works

`index_code` reads files on the Samvit server, not files on a teammate's laptop.
Mount the repository when starting Compose:

```dotenv
SAMVIT_WORKSPACE=/absolute/path/to/your/project
```

Compose mounts that directory read-only as `/workspace`. Ask an agent:

```text
Index /workspace as repository my-product.
```

Do not pass a path such as `/Users/alice/project` from a remote laptop. That path
does not exist inside the Samvit container.

## Recommended Team Routine

At the start of a session:

```text
Read my unread Samvit messages.
List pending tasks relevant to backend.
Recall recent global decisions about the feature I am working on.
```

Before editing:

```text
Claim the task I am about to work on.
```

After an important discovery:

```text
Remember this globally with a clear sentence and source file.
```

At handoff:

```text
Mark the task done with a short result.
Send the reviewer a message with the relevant memory key or file.
```

## Troubleshooting

### The client cannot connect

From the client machine:

```bash
curl http://SAMVIT_HOST:8765/health
```

If this fails, check the VPN, firewall, server bind address, and Docker status.

### The server connects but tools fail with 401

The bearer token is missing, mistyped, or was rotated. Register a new handle or
ask the administrator to reset the existing handle.

### Claude shows the server as pending

Run `/mcp` and approve the server if prompted. Check `claude mcp get samvit` for
the configured URL and headers.

### Code indexing rejects a path

The path must be inside `SAMVIT_CODE_ROOTS`, which defaults to `/workspace` in
Docker Compose.

### A long-running task becomes available again

Call `renew` before the 30-minute claim timeout. Workers should renew leases
periodically while executing long jobs.

## Production Checklist

- Use a private VPN or TLS reverse proxy.
- Replace all default secrets.
- Back up the PostgreSQL volume.
- Give every person or service its own token.
- Keep repositories mounted read-only.
- Set `SAMVIT_CORS_ORIGINS` to known browser origins.
- Monitor `/ready` and Docker container health.
- Review guard violations for repeated credential-sharing attempts.
