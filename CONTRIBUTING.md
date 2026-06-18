# Contributing

Samvit welcomes focused fixes and integrations.

1. Open an issue before large behavioral or schema changes.
2. Create a branch and keep changes scoped.
3. Start PostgreSQL and Redpanda with `docker compose up -d postgres redpanda`.
4. Install development dependencies with `pip install -e ".[dev]"`.
5. Run `pytest -v`.
6. Update documentation and `CHANGELOG.md` when behavior changes.

Database changes must use a new numbered migration. Never edit a migration that
may already have been applied by users.
