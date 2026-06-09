-- Migration 005: add worker_type to tasks for dispatcher routing
-- worker_type is the tag a dispatcher uses to route tasks to the right worker class.

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS worker_type TEXT;
CREATE INDEX IF NOT EXISTS idx_tasks_worker_type ON tasks (worker_type, status)
    WHERE status = 'pending';

INSERT INTO schema_migrations (version) VALUES (5)
    ON CONFLICT (version) DO NOTHING;
