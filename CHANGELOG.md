# Changelog

## 0.2.0 - 2026-06-10

- Added an authenticated HTTP tool bridge for workers, hooks, and integrations.
- Added task creation, listing, cancellation, idempotency, and lease renewal.
- Made task completion ownership-safe and atomic.
- Made message consumption safe under concurrent readers.
- Fixed semantic recall.
- Added readiness and database-backed metrics endpoints.
- Restricted code indexing to configured server roots.
- Added the `samvit` CLI and packaged SQL migrations in Python wheels.
- Added a complete team usage guide and two-machine deployment walkthrough.
- Repositioned and rebuilt the website around cross-tool coding-agent coordination.

## 0.1.0 - 2026-06-09

- Initial memory, task, messaging, RAG, code graph, guard, and MCP server.
