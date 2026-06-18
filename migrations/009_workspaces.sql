-- Migration 009: Workspace/team isolation
--
-- Every agent and data row is scoped to a workspace.
-- A default workspace is created for existing data.

CREATE TABLE workspaces (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL UNIQUE CHECK (name ~ '^[a-z0-9_-]{1,64}$'),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Default workspace for existing/self-registered agents
INSERT INTO workspaces (id, name)
VALUES (gen_random_uuid(), 'default');

-- Add workspace_id to agents
ALTER TABLE agents ADD COLUMN workspace_id UUID REFERENCES workspaces(id);

-- Backfill existing agents into default workspace
UPDATE agents SET workspace_id = (SELECT id FROM workspaces WHERE name = 'default');

ALTER TABLE agents ALTER COLUMN workspace_id SET NOT NULL;
CREATE INDEX idx_agents_workspace ON agents (workspace_id);

INSERT INTO schema_migrations (version) VALUES (9)
    ON CONFLICT (version) DO NOTHING;
