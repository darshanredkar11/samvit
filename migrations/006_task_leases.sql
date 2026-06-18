-- Migration 006: task lease renewal metadata and task idempotency.

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_creator_idempotency
    ON tasks (created_by, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

INSERT INTO schema_migrations (version) VALUES (6)
    ON CONFLICT (version) DO NOTHING;
