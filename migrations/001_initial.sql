-- Samvit — Initial Schema
-- All decisions from FAILURE_ANALYSIS.md §11 are baked in.

-- ── Extensions ────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ── Migration tracking ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INT PRIMARY KEY,
    applied_at  TIMESTAMPTZ DEFAULT now()
);

-- ── Agents ────────────────────────────────────────────────────────────────────
-- Decision #1: handle validated by CHECK constraint (matches ^[a-z0-9_-]{1,64}$)
-- Decision #2: application layer lowercases before insert; constraint enforces result
CREATE TABLE agents (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    handle      TEXT        NOT NULL UNIQUE
                            CHECK (handle ~ '^[a-z0-9_-]{1,64}$'),
    provider    TEXT        NOT NULL CHECK (length(provider) > 0),
    token_hash  TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── KV Memory ─────────────────────────────────────────────────────────────────
-- Decision #3: namespace is free-form; 'global' is the shared namespace;
--              application enforces that agents only write to own handle or 'global'
CREATE TABLE kv_memory (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    UUID        NOT NULL REFERENCES agents(id),
    namespace   TEXT        NOT NULL CHECK (length(namespace) > 0),
    key         TEXT        NOT NULL CHECK (length(key) > 0),
    value       JSONB       NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace, key)  -- one value per key per namespace (global = shared)
);
-- Decision #1 (index): covering index for fast key lookups beyond unique constraint
CREATE INDEX idx_kv_memory_lookup ON kv_memory (namespace, key);

-- ── Semantic Memory ───────────────────────────────────────────────────────────
-- embedding_model stored so future model changes are detectable (§11 review)
-- IVFFlat index requires ≥ lists rows (100) to be efficient;
-- pgvector falls back to seq scan automatically on small tables.
CREATE TABLE semantic_memory (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID        NOT NULL REFERENCES agents(id),
    namespace       TEXT        NOT NULL CHECK (length(namespace) > 0),
    content         TEXT        NOT NULL CHECK (length(content) > 0),
    embedding       VECTOR(384),
    embedding_model TEXT        NOT NULL DEFAULT 'all-MiniLM-L6-v2',
    metadata        JSONB       NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_semantic_memory_namespace ON semantic_memory (namespace);
CREATE INDEX idx_semantic_memory_ivfflat
    ON semantic_memory USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ── Tasks ─────────────────────────────────────────────────────────────────────
-- Decision: 'cancelled' reserved for future use, included in enum now to
--           avoid ALTER TYPE migration later.
CREATE TYPE task_status AS ENUM ('pending', 'claimed', 'done', 'failed', 'cancelled');

CREATE TABLE tasks (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT        NOT NULL CHECK (length(title) > 0),
    description     TEXT,
    status          task_status NOT NULL DEFAULT 'pending',
    created_by      UUID        REFERENCES agents(id),
    claimed_by      UUID        REFERENCES agents(id),
    claim_token     TEXT,
    tags            TEXT[]      NOT NULL DEFAULT '{}',
    priority        INT         NOT NULL DEFAULT 0,
    deadline        TIMESTAMPTZ,
    -- Decision #8: grace period applied in application layer
    --              cleanup fires at claimed_at + claim_timeout + 5 min
    claim_timeout   INTERVAL    NOT NULL DEFAULT '30 minutes',
    claimed_at      TIMESTAMPTZ,
    done_at         TIMESTAMPTZ,
    result          JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Partial index on pending tasks only — the hot path for claim
CREATE INDEX idx_tasks_pending
    ON tasks (priority DESC, created_at ASC)
    WHERE status = 'pending';

-- GIN index for tag OR-filter  (Decision #15: tags && $1::text[])
CREATE INDEX idx_tasks_tags ON tasks USING gin (tags);

-- ── Messages ──────────────────────────────────────────────────────────────────
CREATE TABLE messages (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    from_agent  UUID        NOT NULL REFERENCES agents(id),
    to_agent    UUID        REFERENCES agents(id),   -- NULL = broadcast
    topic       TEXT,
    body        TEXT        NOT NULL CHECK (length(body) > 0),
    metadata    JSONB       NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_messages_directed  ON messages (to_agent,  created_at) WHERE to_agent IS NOT NULL;
CREATE INDEX idx_messages_broadcast ON messages (created_at)             WHERE to_agent IS NULL;
CREATE INDEX idx_messages_topic     ON messages (topic, created_at)      WHERE topic IS NOT NULL;

-- ── Message Reads (Decision #11) ──────────────────────────────────────────────
-- Replaces read_by UUID[] column which would grow unboundedly for broadcasts.
CREATE TABLE message_reads (
    message_id  UUID        NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    agent_id    UUID        NOT NULL REFERENCES agents(id)   ON DELETE CASCADE,
    read_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, agent_id)
);
CREATE INDEX idx_message_reads_agent ON message_reads (agent_id, read_at);

-- ── Stamp migration version ───────────────────────────────────────────────────
INSERT INTO schema_migrations (version) VALUES (1)
    ON CONFLICT (version) DO NOTHING;
