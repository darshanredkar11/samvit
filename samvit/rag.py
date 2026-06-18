"""
RAG document ingestion and chunking.

Supported formats:
  .pdf        — extract text via pdfplumber (optional dep)
  .md / .txt  — plain text; .md extracts [[wikilinks]] for the graph
  .py .js .ts .go .rs .java  — source files (treated as text, special metadata)

Chunking strategy:
  - Split at natural boundaries (paragraphs, headings, blank lines)
  - Target ~400 tokens (~1600 chars) per chunk
  - 10% overlap: last sentence(s) of previous chunk prepended to next
  - Each chunk embedded independently

Guards are applied to every chunk before storage.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import AsyncIterator

from samvit import db, embeddings, guard

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

CHUNK_SIZE_CHARS   = 1600   # ~400 tokens for BAAI/bge-small-en-v1.5
CHUNK_OVERLAP_CHARS = 160   # ~10% overlap
MAX_DOCUMENT_BYTES  = 10 * 1024 * 1024   # 10 MB hard limit per document

# ── Text extraction ───────────────────────────────────────────────────────────

def extract_text(content: bytes | str, filename: str) -> str:
    """Extract plain text from document content."""
    suffix = Path(filename).suffix.lower()

    if isinstance(content, str):
        return content

    if suffix == ".pdf":
        return _extract_pdf(content)

    # Markdown, plain text, source code
    try:
        return content.decode("utf-8", errors="replace")
    except Exception:
        return content.decode("latin-1", errors="replace")


def _extract_pdf(data: bytes) -> str:
    try:
        import pdfplumber, io
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n\n".join(
                page.extract_text() or "" for page in pdf.pages
            )
    except ImportError:
        try:
            import pypdf, io
            reader = pypdf.PdfReader(io.BytesIO(data))
            return "\n\n".join(
                page.extract_text() or "" for page in reader.pages
            )
        except ImportError:
            raise RuntimeError(
                "PDF support requires 'pdfplumber' or 'pypdf': "
                "pip install pdfplumber"
            )


# ── Wikilink extraction (Obsidian) ─────────────────────────────────────────────

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")


def extract_wikilinks(text: str) -> list[str]:
    """Extract [[wikilink]] targets from Obsidian markdown."""
    return [m.group(1).strip() for m in _WIKILINK_RE.finditer(text)]


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_text(text: str) -> list[dict]:
    """
    Split text into overlapping chunks.
    Returns list of {content, char_start, char_end, chunk_index}.
    """
    # Split at paragraph boundaries (blank lines or markdown headings)
    paragraphs = re.split(r"\n{2,}|(?=\n#{1,6}\s)", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks: list[dict] = []
    current = ""
    current_start = 0
    char_pos = 0
    overlap_tail = ""

    for para in paragraphs:
        candidate = (overlap_tail + "\n\n" + current + "\n\n" + para).strip() \
                    if current else (overlap_tail + "\n\n" + para).strip() \
                    if overlap_tail else para

        if len(candidate) > CHUNK_SIZE_CHARS and current:
            # Flush current chunk
            chunk_text_val = (overlap_tail + "\n\n" + current).strip() \
                              if overlap_tail else current
            chunk_start = char_pos - len(current)
            chunks.append({
                "content":     chunk_text_val,
                "char_start":  chunk_start,
                "char_end":    char_pos,
                "chunk_index": len(chunks),
            })
            # Overlap: last CHUNK_OVERLAP_CHARS of current
            overlap_tail = current[-CHUNK_OVERLAP_CHARS:]
            current = para
        else:
            current = candidate if not current else current + "\n\n" + para

        char_pos += len(para) + 2

    # Flush last chunk
    if current:
        chunk_text_val = (overlap_tail + "\n\n" + current).strip() \
                          if overlap_tail else current
        chunks.append({
            "content":     chunk_text_val,
            "char_start":  char_pos - len(current),
            "char_end":    char_pos,
            "chunk_index": len(chunks),
        })

    return chunks if chunks else [{"content": text, "char_start": 0, "char_end": len(text), "chunk_index": 0}]


# ── Ingest ────────────────────────────────────────────────────────────────────

async def ingest(
    agent: dict,
    content: str | bytes,
    filename: str,
    description: str | None = None,
    namespace: str = "global",
    content_type: str | None = None,
) -> dict:
    """
    Ingest a document: extract text, chunk, embed, store.
    Returns {document_id, chunk_count, wikilinks}.
    """
    if isinstance(content, bytes) and len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"Document exceeds {MAX_DOCUMENT_BYTES // (1024*1024)} MB limit")
    if isinstance(content, str) and len(content.encode()) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"Document exceeds {MAX_DOCUMENT_BYTES // (1024*1024)} MB limit")

    suffix = Path(filename).suffix.lower()
    ct = content_type or _content_type(suffix)

    raw_text = extract_text(content, filename)
    if not raw_text.strip():
        raise ValueError("Document appears to be empty or unreadable")

    # Extract Obsidian wikilinks before guard (guard may redact content)
    wikilinks: list[str] = []
    if suffix == ".md":
        wikilinks = extract_wikilinks(raw_text)

    chunks = chunk_text(raw_text)
    document_id = str(uuid.uuid4())

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            # Insert document record
            await conn.execute(
                """
                INSERT INTO documents
                    (id, agent_id, filename, content_type, description,
                     char_count, chunk_count, namespace, workspace_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                document_id, agent["id"], filename, ct, description,
                len(raw_text), len(chunks), namespace, agent["workspace_id"],
            )

            # Embed and insert each chunk
            for chunk in chunks:
                # Guard: scan chunk before storing
                clean_content = await guard.apply(
                    chunk["content"], agent["id"], "input", "ingest"
                )
                vec = await embeddings.embed(clean_content)
                vector = embeddings.fmt_vector(vec)
                await conn.execute(
                    """
                    INSERT INTO document_chunks
                        (document_id, chunk_index, content, embedding,
                         char_start, char_end)
                    VALUES ($1, $2, $3, $4::vector, $5, $6)
                    """,
                    document_id, chunk["chunk_index"], clean_content,
                    vector, chunk["char_start"], chunk["char_end"],
                )

            # Store wikilinks
            if wikilinks:
                await conn.executemany(
                    """
                    INSERT INTO doc_links (from_doc, to_filename, link_text)
                    VALUES ($1, $2, $3)
                    ON CONFLICT DO NOTHING
                    """,
                    [(document_id, lnk, lnk) for lnk in wikilinks],
                )
                # Resolve links to existing documents
                await conn.execute(
                    """
                    UPDATE doc_links dl
                       SET to_doc = d.id
                      FROM documents d
                     WHERE dl.from_doc = $1
                       AND dl.to_doc IS NULL
                       AND d.filename LIKE '%' || dl.to_filename || '%'
                    """,
                    document_id,
                )

    log.info("Ingested document: %s  chunks=%d  wikilinks=%d",
             filename, len(chunks), len(wikilinks))

    return {
        "document_id": document_id,
        "filename":    filename,
        "chunk_count": len(chunks),
        "char_count":  len(raw_text),
        "wikilinks":   wikilinks,
    }


# ── Search ────────────────────────────────────────────────────────────────────

async def search_docs(
    agent: dict,
    query: str,
    filename_filter: str | None = None,
    namespace: str = "global",
    limit: int = 5,
    min_score: float = 0.3,
) -> dict:
    """
    Semantic search across all ingested document chunks.
    Returns the most relevant chunks with their source document info.
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    limit = min(limit, 20)

    vector = embeddings.fmt_vector(await embeddings.embed(query))

    async with db.pool().acquire() as conn:
        row_count = await conn.fetchval(
            "SELECT COUNT(*) FROM document_chunks"
        )
        if row_count < 100:
            await conn.execute("SET LOCAL enable_indexscan = off")

        params: list = [vector, namespace, min_score, limit, agent["workspace_id"]]
        filename_clause = ""
        if filename_filter:
            params.append(f"%{filename_filter}%")
            filename_clause = f"AND d.filename ILIKE ${len(params)}"

        rows = await conn.fetch(
            f"""
            SELECT
                dc.id,
                dc.content,
                dc.chunk_index,
                dc.char_start,
                dc.char_end,
                1 - (dc.embedding <=> $1::vector) AS score,
                d.id          AS doc_id,
                d.filename,
                d.description,
                d.content_type
              FROM document_chunks dc
              JOIN documents d ON d.id = dc.document_id
             WHERE d.namespace = $2
               AND d.workspace_id = $5
               AND 1 - (dc.embedding <=> $1::vector) >= $3
               {filename_clause}
             ORDER BY dc.embedding <=> $1::vector
             LIMIT $4
            """,
            *params,
        )

    # Guard: scan results before returning to LLM
    results = []
    for r in rows:
        clean = await guard.apply(r["content"], agent["id"], "output", "search_docs",
                                  check_entropy=False)
        results.append({
            "chunk_id":    str(r["id"]),
            "content":     clean,
            "score":       round(float(r["score"]), 4),
            "document":    r["filename"],
            "description": r["description"],
            "doc_id":      str(r["doc_id"]),
            "chunk_index": r["chunk_index"],
        })

    return {"results": results, "query": query}


async def get_linked_docs(document_id: str) -> dict:
    """Return all documents linked to/from a given document (Obsidian graph traversal)."""
    async with db.pool().acquire() as conn:
        outbound = await conn.fetch(
            """
            SELECT dl.to_filename, d.id, d.filename, d.description
              FROM doc_links dl
              LEFT JOIN documents d ON d.id = dl.to_doc
             WHERE dl.from_doc = $1
            """,
            document_id,
        )
        inbound = await conn.fetch(
            """
            SELECT d.id, d.filename, d.description
              FROM doc_links dl
              JOIN documents d ON d.id = dl.from_doc
             WHERE dl.to_doc = $1
            """,
            document_id,
        )
    return {
        "links_to":     [{"filename": r["to_filename"], "doc_id": str(r["id"]) if r["id"] else None} for r in outbound],
        "linked_from":  [{"doc_id": str(r["id"]), "filename": r["filename"]} for r in inbound],
    }


def _content_type(suffix: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".md":  "text/markdown",
        ".txt": "text/plain",
        ".py":  "text/x-python",
        ".js":  "text/javascript",
        ".ts":  "text/typescript",
        ".go":  "text/x-go",
        ".rs":  "text/x-rust",
        ".java":"text/x-java",
    }.get(suffix, "text/plain")
