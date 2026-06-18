# Samvit Gap Tracker

This file tracks release gaps found during the June 2026 product and implementation
review. Priorities describe launch impact:

- P0: advertised core workflow is broken or unsafe
- P1: required for a credible public open-source release
- P2: important product maturity work
- P3: longer-term differentiation

## Release Blockers

| ID | Priority | Gap | Status |
|---|---:|---|---|
| CORE-01 | P0 | Semantic recall references an undefined agent | Fixed |
| CORE-02 | P0 | Workers, hooks, dispatcher, and Hermes call a nonexistent `/mcp/call` route | Fixed |
| CORE-03 | P0 | Task completion is not atomic and does not verify the claiming agent | Fixed |
| CORE-04 | P0 | Concurrent message reads can deliver the same message twice | Fixed |
| CORE-05 | P0 | Metrics endpoint queries nonexistent fields and functions | Fixed |
| CORE-06 | P0 | Dispatcher stores `worker_type` but claims by tags | Fixed |
| CORE-07 | P0 | No supported way for agents to create tasks through MCP | Fixed |

## Release Quality

| ID | Priority | Gap | Status |
|---|---:|---|---|
| REL-01 | P1 | No task listing, cancellation, or lease renewal | Fixed |
| REL-02 | P1 | Code indexing accepts arbitrary server filesystem paths | Fixed |
| REL-03 | P1 | Health endpoint does not distinguish liveness from readiness | Fixed |
| REL-04 | P1 | Legacy SSE is the only documented MCP transport | Fixed |
| REL-05 | P1 | Docker images and Python dependencies are not reproducibly pinned | Partial |
| REL-06 | P1 | No CI workflow or lightweight unit-test path | Fixed |
| REL-07 | P1 | README and spec drift from the implementation | Fixed |
| REL-08 | P1 | New RAG, code graph, HTTP bridge, and worker flows lack integration coverage | Partial |
| REL-09 | P1 | No security policy, contributor guide, or changelog | Fixed |

## Product Gaps

| ID | Priority | Gap | Status |
|---|---:|---|---|
| PROD-01 | P1 | Positioning is too broad and competes directly with memory/orchestration frameworks | Fixed |
| PROD-02 | P1 | No beginner team onboarding guide | Fixed |
| PROD-03 | P1 | No two-machine Claude Code and Antigravity deployment guide | Fixed |
| PROD-04 | P1 | Website lacks focused search intent, metadata, structured data, and crawl files | Fixed |
| PROD-05 | P2 | No workspace/team object; `global` is shared by every registered agent | Open |
| PROD-06 | P2 | No agent capability registry or online presence | Open |
| PROD-07 | P2 | No task dependencies, retries, status history, or approval gates | Open |
| PROD-08 | P2 | No memory delete/update lifecycle, deduplication, retention, or provenance policy | Open |
| PROD-09 | P2 | Redpanda adds operational weight without a bundled event consumer | Open |
| PROD-10 | P3 | No file-intent declaration or cross-agent edit conflict detection | Open |
| PROD-11 | P3 | No A2A compatibility layer | Open |
| PROD-12 | P3 | No reproducible multi-agent coordination benchmark | Open |

## Recommended Next Milestone

The next release should focus on workspace isolation and conflict-aware coding:

1. Add teams/workspaces and scoped credentials.
2. Let agents declare files and symbols they intend to modify.
3. Detect overlapping active work before edits begin.
4. Add task history, dependencies, retries, and approval gates.
5. Publish a benchmark comparing two coding agents with and without Samvit.
