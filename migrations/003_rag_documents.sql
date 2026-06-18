-- Migration 003: RAG document store
--
-- Documents are chunked and embedded on ingest.
-- All agents can search across all ingested documents.
-- Obsidian .md files with [[wikilinks]] generate graph edges automatically.

-- ── Documents ────────────────────────────────────────────────────────────────
CREATE TABLE documents (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id     UUID        NOT NULL REFERENCES agents(id),
    filename     TEXT        NOT NULL,
    content_type TEXT        NOT NULL DEFAULT 'text/plain',
    description  TEXT,
    char_count   INT         NOT NULL DEFAULT 0,
    chunk_count  INT         NOT NULL DEFAULT 0,
    namespace    TEXT        NOT NULL DEFAULT 'global',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_documents_agent     ON documents (agent_id);
CREATE INDEX idx_documents_namespace ON documents (namespace, created_at DESC);
CREATE INDEX idx_documents_filename  ON documents (filename);

-- ── Document chunks (the searchable units) ───────────────────────────────────
CREATE TABLE document_chunks (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INT         NOT NULL,
    content         TEXT        NOT NULL,
    embedding       VECTOR(384),
    embedding_model TEXT        NOT NULL DEFAULT 'BAAI/bge-small-en-v1.5',
    char_start      INT,
    char_end        INT,
    metadata        JSONB       NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX idx_chunks_document ON document_chunks (document_id, chunk_index);
CREATE INDEX idx_chunks_ivfflat
    ON document_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ── Obsidian wikilink graph ───────────────────────────────────────────────────
-- [[wikilinks]] extracted from .md files become edges in the knowledge graph
CREATE TABLE doc_links (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_doc    UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    to_filename TEXT NOT NULL,     -- target filename (may not exist yet)
    to_doc      UUID REFERENCES documents(id) ON DELETE SET NULL,  -- resolved when target ingested
    link_text   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_doc_links_from ON doc_links (from_doc);
CREATE INDEX idx_doc_links_to   ON doc_links (to_doc);

INSERT INTO schema_migrations (version) VALUES (3)
    ON CONFLICT (version) DO NOTHING;
