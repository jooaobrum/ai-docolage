"""Example downstream pipeline: Load -> Process -> Chunk -> Store embeddings.

Runs against an already-running docolage_service Postgres (the same one
`docker compose up` provisions) -- it reads completed/partial jobs'
page_results (already produced by the Worker) and writes docs_metadata +
docs_chunks (see db/migrations/0002_chunks.sql). It never touches object
storage or re-parses anything; Load starts from what the Service already
persisted.

Usage:
    DOCOLAGE_POSTGRES_DSN=postgresql://docolage:docolage@localhost:5432/docolage \\
    OPENAI_API_KEY=sk-... \\
    python -m examples.rag_pipeline.pipeline [--job-id UUID] [--all]

--job-id processes one job; --all processes every completed/partial job
that doesn't have a docs_metadata row yet. With neither flag, --all is
the default.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os

import asyncpg

from examples.rag_pipeline.chunking import Chunk, build_page_chunks
from examples.rag_pipeline.openai_client import OpenAIPipelineClient, build_pipeline_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("rag_pipeline")


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

async def load_candidate_jobs(pool: asyncpg.Pool, job_id: str | None) -> list[asyncpg.Record]:
    """Jobs eligible for chunking: completed or partial (partial still has
    real successful pages worth chunking), not yet in docs_metadata."""
    async with pool.acquire() as conn:
        if job_id is not None:
            return await conn.fetch(
                "SELECT id, original_filename, format, total_pages, status "
                "FROM jobs WHERE id = $1::uuid AND status IN ('completed', 'partial')",
                job_id,
            )
        return await conn.fetch(
            """
            SELECT j.id, j.original_filename, j.format, j.total_pages, j.status
            FROM jobs j
            LEFT JOIN docs_metadata dm ON dm.job_id = j.id
            WHERE j.status IN ('completed', 'partial') AND dm.id IS NULL
            ORDER BY j.created_at
            """
        )


async def load_page_results(pool: asyncpg.Pool, job_id) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT page_number, markdown, elements FROM page_results "
            "WHERE job_id = $1::uuid ORDER BY page_number",
            job_id,
        )


# ---------------------------------------------------------------------------
# Process + Chunk
# ---------------------------------------------------------------------------

def process_pages_into_chunks(page_rows: list[asyncpg.Record]) -> list[Chunk]:
    """Process: parse each page's stored elements JSON. Chunk: hand off
    to chunking.build_page_chunks for the actual section/image splitting.
    Kept as two named steps (per the Load/Process/Chunk/Store framing)
    even though the "processing" here is just JSON decoding -- a
    heavier per-element normalization step would live here without
    changing chunking.py's contract."""
    all_chunks: list[Chunk] = []
    for row in page_rows:
        elements = json.loads(row["elements"]) if row["elements"] else []
        all_chunks.extend(build_page_chunks(row["page_number"], elements))
    return all_chunks


# ---------------------------------------------------------------------------
# Store embeddings
# ---------------------------------------------------------------------------

async def store_doc_metadata(
    pool: asyncpg.Pool,
    *,
    job_id,
    original_filename: str,
    format: str,
    total_pages: int,
    doc_summary: str | None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO docs_metadata (job_id, original_filename, format, total_pages, doc_summary)
            VALUES ($1::uuid, $2, $3, $4, $5)
            ON CONFLICT (job_id) DO UPDATE
                SET doc_summary = EXCLUDED.doc_summary, updated_at = now()
            """,
            job_id,
            original_filename,
            format,
            total_pages,
            doc_summary,
        )


async def store_chunks(
    pool: asyncpg.Pool,
    *,
    job_id,
    chunks: list[Chunk],
    embeddings: list[list[float] | None],
    embedding_model: str | None,
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        for chunk, embedding in zip(chunks, embeddings):
            await conn.execute(
                """
                INSERT INTO docs_chunks
                    (job_id, page_number, chunk_index, chunk_type, section, content, embedding, embedding_model)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (job_id, page_number, chunk_index) DO UPDATE
                    SET chunk_type = EXCLUDED.chunk_type,
                        section = EXCLUDED.section,
                        content = EXCLUDED.content,
                        embedding = EXCLUDED.embedding,
                        embedding_model = EXCLUDED.embedding_model
                """,
                job_id,
                chunk.page_number,
                chunk.chunk_index,
                chunk.chunk_type,
                chunk.section,
                chunk.content,
                embedding,
                embedding_model,
            )


def _embedding_input(chunk: Chunk) -> str:
    """Embed 'section + chunk' together (not the chunk alone) so a
    retrieved chunk's vector already reflects the context it was found
    under, per the requirement that embeddings run on section+chunk."""
    if chunk.section:
        return f"Section: {chunk.section}\n\n{chunk.content}"
    return chunk.content


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def process_job(pool: asyncpg.Pool, job: asyncpg.Record, client: OpenAIPipelineClient | None) -> list[Chunk]:
    job_id = job["id"]
    logger.info("load: job_id=%s filename=%s pages=%s", job_id, job["original_filename"], job["total_pages"])

    page_rows = await load_page_results(pool, job_id)

    logger.info("process+chunk: job_id=%s", job_id)
    chunks = process_pages_into_chunks(page_rows)
    logger.info(
        "chunk: job_id=%s produced %s chunks (%s image, %s text)",
        job_id,
        len(chunks),
        sum(1 for c in chunks if c.chunk_type == "image"),
        sum(1 for c in chunks if c.chunk_type == "text"),
    )

    if client is not None:
        doc_summary = client.summarize_document(
            job["original_filename"], [r["markdown"] or "" for r in page_rows]
        )
        embeddings: list[list[float] | None] = client.embed_texts([_embedding_input(c) for c in chunks])
        embedding_model = client.embedding_model
    else:
        doc_summary = f"{job['original_filename']} ({job['total_pages']} pages) -- summary unavailable, no OPENAI_API_KEY configured."
        embeddings = [None for _ in chunks]
        embedding_model = None

    logger.info("store: job_id=%s doc_summary=%r", job_id, doc_summary[:80])
    await store_doc_metadata(
        pool,
        job_id=job_id,
        original_filename=job["original_filename"],
        format=job["format"],
        total_pages=job["total_pages"],
        doc_summary=doc_summary,
    )
    await store_chunks(pool, job_id=job_id, chunks=chunks, embeddings=embeddings, embedding_model=embedding_model)

    return chunks


async def run(job_id: str | None) -> None:
    dsn = os.environ.get("DOCOLAGE_POSTGRES_DSN", "postgresql://docolage:docolage@localhost:5432/docolage")
    pool = await asyncpg.create_pool(dsn=dsn)
    client = build_pipeline_client()

    try:
        jobs = await load_candidate_jobs(pool, job_id)
        if not jobs:
            logger.info("no candidate jobs found (job_id=%s)", job_id)
            return

        for job in jobs:
            chunks = await process_job(pool, job, client)
            print(f"\n=== job {job['id']} ({job['original_filename']}) -- {len(chunks)} chunks ===")
            for c in chunks:
                print(f"--- page {c.page_number} chunk {c.chunk_index} [{c.chunk_type}] section={c.section!r} ---")
                print(c.content)
                print()
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", default=None, help="Process a single job by id.")
    parser.add_argument("--all", action="store_true", help="Process every completed/partial job not yet chunked (default).")
    args = parser.parse_args()
    asyncio.run(run(args.job_id))


if __name__ == "__main__":
    main()
