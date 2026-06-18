-- Migration 010: Add workspace_id to all data tables
--
-- Tables with implicit scoping via FK relationships are left unchanged:
--   message_reads    → scoped via messages
--   document_chunks  → scoped via documents
--   doc_links        → scoped via documents
--   system_settings  → global
--   admin_audit_log  → system-level (kept global)

-- ── Helper: drop a unique constraint by column names ──────────────────────────
DO $$ BEGIN
    EXECUTE (
        SELECT 'ALTER TABLE kv_memory DROP CONSTRAINT IF EXISTS ' || conname
        FROM pg_constraint
        WHERE conrelid = 'kv_memory'::regclass AND contype = 'u'
          AND conkey = (
              SELECT array_agg(attnum ORDER BY attnum)
              FROM pg_attribute
              WHERE attrelid = 'kv_memory'::regclass AND attname IN ('namespace', 'key')
          )
        LIMIT 1
    );
END $$;

DO $$ BEGIN
    EXECUTE (
        SELECT 'ALTER TABLE code_nodes DROP CONSTRAINT IF EXISTS ' || conname
        FROM pg_constraint
        WHERE conrelid = 'code_nodes'::regclass AND contype = 'u'
          AND conkey = (
              SELECT array_agg(attnum ORDER BY attnum)
              FROM pg_attribute
              WHERE attrelid = 'code_nodes'::regclass AND attname IN ('repo_id', 'qualified')
          )
        LIMIT 1
    );
END $$;

DO $$ BEGIN
    EXECUTE (
        SELECT 'ALTER TABLE code_repos DROP CONSTRAINT IF EXISTS ' || conname
        FROM pg_constraint
        WHERE conrelid = 'code_repos'::regclass AND contype = 'u'
          AND conkey = (
              SELECT array_agg(attnum ORDER BY attnum)
              FROM pg_attribute
              WHERE attrelid = 'code_repos'::regclass AND attname IN ('repo_id')
          )
        LIMIT 1
    );
END $$;

-- ── tasks ─────────────────────────────────────────────────────────────────────
ALTER TABLE tasks ADD COLUMN workspace_id UUID REFERENCES workspaces(id);
UPDATE tasks SET workspace_id = (SELECT id FROM workspaces WHERE name = 'default');
ALTER TABLE tasks ALTER COLUMN workspace_id SET NOT NULL;
CREATE INDEX idx_tasks_workspace ON tasks (workspace_id);
DROP INDEX IF EXISTS idx_tasks_pending;
CREATE INDEX idx_tasks_pending
    ON tasks (workspace_id, priority DESC, created_at ASC)
    WHERE status = 'pending';

-- ── messages ──────────────────────────────────────────────────────────────────
ALTER TABLE messages ADD COLUMN workspace_id UUID REFERENCES workspaces(id);
UPDATE messages SET workspace_id = (SELECT id FROM workspaces WHERE name = 'default');
ALTER TABLE messages ALTER COLUMN workspace_id SET NOT NULL;
CREATE INDEX idx_messages_workspace ON messages (workspace_id);

-- ── kv_memory ─────────────────────────────────────────────────────────────────
ALTER TABLE kv_memory ADD COLUMN workspace_id UUID REFERENCES workspaces(id);
UPDATE kv_memory SET workspace_id = (SELECT id FROM workspaces WHERE name = 'default');
ALTER TABLE kv_memory ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE kv_memory ADD CONSTRAINT kv_memory_ws_ns_key UNIQUE (workspace_id, namespace, key);
CREATE INDEX idx_kv_memory_ws_lookup ON kv_memory (workspace_id, namespace, key);

-- ── semantic_memory ───────────────────────────────────────────────────────────
ALTER TABLE semantic_memory ADD COLUMN workspace_id UUID REFERENCES workspaces(id);
UPDATE semantic_memory SET workspace_id = (SELECT id FROM workspaces WHERE name = 'default');
ALTER TABLE semantic_memory ALTER COLUMN workspace_id SET NOT NULL;
CREATE INDEX idx_semantic_memory_ws_namespace ON semantic_memory (workspace_id, namespace);

-- ── guard_violations ──────────────────────────────────────────────────────────
ALTER TABLE guard_violations ADD COLUMN workspace_id UUID REFERENCES workspaces(id);
UPDATE guard_violations SET workspace_id = (SELECT id FROM workspaces WHERE name = 'default');
ALTER TABLE guard_violations ALTER COLUMN workspace_id SET NOT NULL;
CREATE INDEX idx_guard_violations_ws_agent ON guard_violations (workspace_id, agent_id, created_at DESC);

-- ── documents ─────────────────────────────────────────────────────────────────
ALTER TABLE documents ADD COLUMN workspace_id UUID REFERENCES workspaces(id);
UPDATE documents SET workspace_id = (SELECT id FROM workspaces WHERE name = 'default');
ALTER TABLE documents ALTER COLUMN workspace_id SET NOT NULL;
CREATE INDEX idx_documents_ws_namespace ON documents (workspace_id, namespace, created_at DESC);
CREATE INDEX idx_documents_ws_filename  ON documents (workspace_id, filename);

-- ── code_nodes ────────────────────────────────────────────────────────────────
ALTER TABLE code_nodes ADD COLUMN workspace_id UUID REFERENCES workspaces(id);
UPDATE code_nodes SET workspace_id = (SELECT id FROM workspaces WHERE name = 'default');
ALTER TABLE code_nodes ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE code_nodes ADD CONSTRAINT code_nodes_ws_repo_qual UNIQUE (workspace_id, repo_id, qualified);
CREATE INDEX idx_nodes_ws_repo ON code_nodes (workspace_id, repo_id);

-- ── code_edges ────────────────────────────────────────────────────────────────
ALTER TABLE code_edges ADD COLUMN workspace_id UUID REFERENCES workspaces(id);
UPDATE code_edges SET workspace_id = (SELECT id FROM workspaces WHERE name = 'default');
ALTER TABLE code_edges ALTER COLUMN workspace_id SET NOT NULL;
CREATE INDEX idx_edges_ws_repo ON code_edges (workspace_id, repo_id);

-- ── code_repos ────────────────────────────────────────────────────────────────
ALTER TABLE code_repos ADD COLUMN workspace_id UUID REFERENCES workspaces(id);
UPDATE code_repos SET workspace_id = (SELECT id FROM workspaces WHERE name = 'default');
ALTER TABLE code_repos ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE code_repos ADD CONSTRAINT code_repos_ws_repo_id UNIQUE (workspace_id, repo_id);

INSERT INTO schema_migrations (version) VALUES (10)
    ON CONFLICT (version) DO NOTHING;
