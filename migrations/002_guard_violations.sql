-- Migration 002: Ethical guard audit table
--
-- Stores every guard violation — which pattern fired, which agent, in which
-- direction and tool call. Never stores the original sensitive text.

CREATE TABLE guard_violations (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id     UUID        REFERENCES agents(id) ON DELETE SET NULL,
    direction    TEXT        NOT NULL CHECK (direction IN ('input', 'output')),
    tool         TEXT        NOT NULL,
    pattern_name TEXT        NOT NULL,
    category     TEXT        NOT NULL,  -- credential | pii | live_data | private_key
    severity     TEXT        NOT NULL,  -- high | medium | low
    snippet      TEXT        NOT NULL,  -- first 6 chars + "…", never the full secret
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_guard_violations_agent   ON guard_violations (agent_id, created_at DESC);
CREATE INDEX idx_guard_violations_pattern ON guard_violations (pattern_name, created_at DESC);

INSERT INTO schema_migrations (version) VALUES (2)
    ON CONFLICT (version) DO NOTHING;
