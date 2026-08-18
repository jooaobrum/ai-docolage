-- docs_metadata / docs_chunks: the example RAG pipeline's storage
-- (examples/rag_pipeline/). Out of the core spec's scope (docs/spec/0002,
-- "Out of Scope": "Chunking and any downstream RAG-prep enhancement
-- remain out of scope... the pipeline's job ends at the parse result")
-- -- this is a downstream example built ON TOP of the parse result, not
-- part of the API contract. Real migrations (not script-local CREATE
-- TABLE) so `docker compose up` provisions them like every other table.
--
-- embedding is a plain float8[] rather than a pgvector `vector` column,
-- to avoid requiring the pgvector extension for an example pipeline;
-- swap to pgvector (`CREATE EXTENSION vector`, `vector(1536)` column,
-- ivfflat/hnsw index) if you outgrow linear-scan cosine similarity.

-- One row per parsed document: identity + a short LLM-generated summary
-- so a human or a retrieval step can tell what a document is about
-- without reading its chunks.
CREATE TABLE IF NOT EXISTS docs_metadata (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id            UUID NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    original_filename TEXT NOT NULL,
    format            TEXT NOT NULL,
    total_pages       INTEGER NOT NULL,
    doc_summary       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per chunk. `section` is the nearest preceding heading's text
-- (or NULL if the chunk precedes any heading) -- embeddings are computed
-- over "section + content" so a chunk retrieved on its own still carries
-- its surrounding context. `chunk_type` distinguishes an ordinary
-- text/table chunk from a standalone 'image' chunk: a figure with a
-- caption is promoted to its own chunk (rather than only inlined as a
-- placeholder in its surrounding text chunk) so it is independently
-- retrievable on its caption's meaning.
CREATE TABLE IF NOT EXISTS docs_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    page_number     INTEGER NOT NULL,
    chunk_index     INTEGER NOT NULL,
    chunk_type      TEXT NOT NULL DEFAULT 'text' CHECK (chunk_type IN ('text', 'image')),
    section         TEXT,
    content         TEXT NOT NULL,
    embedding       DOUBLE PRECISION[],
    embedding_model TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_id, page_number, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_docs_chunks_job
    ON docs_chunks (job_id, page_number, chunk_index);
