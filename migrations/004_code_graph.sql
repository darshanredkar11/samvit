-- Migration 004: Code knowledge graph
--
-- Parsed from source files by index_code().
-- Nodes = symbols (files, functions, classes, methods).
-- Edges = relationships (imports, calls, defines, inherits).
-- Agents query with explore_code() or graph_symbol() — never read raw files again.

-- ── Code nodes ───────────────────────────────────────────────────────────────
CREATE TYPE node_type AS ENUM (
    'file', 'function', 'class', 'method', 'import', 'variable'
);

CREATE TABLE code_nodes (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id     TEXT        NOT NULL,       -- user-defined label, e.g. "samvit" or "my-app"
    node_type   node_type   NOT NULL,
    name        TEXT        NOT NULL,
    qualified   TEXT        NOT NULL,       -- e.g. "samvit.auth.verify_token"
    file_path   TEXT        NOT NULL,
    line_start  INT,
    line_end    INT,
    signature   TEXT,                       -- function/method signature
    docstring   TEXT,                       -- first docstring if available
    language    TEXT        NOT NULL DEFAULT 'python',
    embedding   VECTOR(384),                -- semantic search over symbol names + docstrings
    embedding_model TEXT    NOT NULL DEFAULT 'all-MiniLM-L6-v2',
    metadata    JSONB       NOT NULL DEFAULT '{}',
    indexed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repo_id, qualified)
);

CREATE INDEX idx_nodes_repo      ON code_nodes (repo_id);
CREATE INDEX idx_nodes_name      ON code_nodes (name);
CREATE INDEX idx_nodes_file      ON code_nodes (repo_id, file_path);
CREATE INDEX idx_nodes_type      ON code_nodes (node_type);
CREATE INDEX idx_nodes_ivfflat
    ON code_nodes USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);

-- ── Code edges ───────────────────────────────────────────────────────────────
CREATE TYPE edge_type AS ENUM (
    'imports',      -- file A imports file B
    'defines',      -- file/class defines function/method
    'calls',        -- function A calls function B
    'inherits',     -- class A inherits from class B
    'uses',         -- function uses variable/constant
    'references'    -- loosely references (e.g. string annotation)
);

CREATE TABLE code_edges (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id     TEXT        NOT NULL,
    from_node   UUID        NOT NULL REFERENCES code_nodes(id) ON DELETE CASCADE,
    to_node     UUID        REFERENCES code_nodes(id) ON DELETE SET NULL,
    to_name     TEXT        NOT NULL,       -- name even if to_node is unresolved
    edge_type   edge_type   NOT NULL,
    weight      REAL        NOT NULL DEFAULT 1.0,
    metadata    JSONB       NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_edges_from ON code_edges (from_node, edge_type);
CREATE INDEX idx_edges_to   ON code_edges (to_node,   edge_type);
CREATE INDEX idx_edges_repo ON code_edges (repo_id);

-- ── Repo index registry ───────────────────────────────────────────────────────
CREATE TABLE code_repos (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id     TEXT        NOT NULL UNIQUE,
    agent_id    UUID        NOT NULL REFERENCES agents(id),
    root_path   TEXT        NOT NULL,
    language    TEXT        NOT NULL,
    file_count  INT         NOT NULL DEFAULT 0,
    node_count  INT         NOT NULL DEFAULT 0,
    edge_count  INT         NOT NULL DEFAULT 0,
    indexed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (version) VALUES (4)
    ON CONFLICT (version) DO NOTHING;
