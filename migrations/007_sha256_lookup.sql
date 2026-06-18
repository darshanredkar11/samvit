ALTER TABLE agents ADD COLUMN IF NOT EXISTS token_hash_sha256 TEXT;

-- Index for fast token lookup (used by authenticate())
CREATE INDEX IF NOT EXISTS idx_agents_token_sha256 ON agents (token_hash_sha256)
    WHERE token_hash_sha256 IS NOT NULL;

-- Backfill for existing agents (computed on next login or rotate)
INSERT INTO schema_migrations (version) VALUES (7);

