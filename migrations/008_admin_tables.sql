-- Admin UI: roles, suspension, settings, audit log

-- Agent role (agent = default, operator/admin/auditor = admin UI access)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'agent'
    CHECK (role IN ('agent', 'operator', 'auditor', 'admin'));

-- Agent suspension (non-NULL = suspended)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ;

-- System settings (KV for UI-visible config)
CREATE TABLE IF NOT EXISTS system_settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Admin audit log (every mutation is recorded)
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_handle TEXT NOT NULL,
    action       TEXT NOT NULL,
    target_type  TEXT,        -- 'agent' | 'task' | 'setting' | 'system'
    target_id    TEXT,
    details      JSONB,
    created_at   TIMESTAMPTZ DEFAULT now()
);

-- Index for audit log queries
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created
    ON admin_audit_log (created_at DESC);

INSERT INTO schema_migrations (version) VALUES (8);
