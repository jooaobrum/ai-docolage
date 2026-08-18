"""Worker claim loop and page parsing (ticket #21).

Drives `claim_and_process_one` directly, in a bounded loop, against real
throwaway Postgres + object storage (same testcontainers-gated harness as
test_health.py/test_jobs.py) — no real subprocess, no sleep-based
polling. The Recognizer is faked (docolage.ports.FakeVLMRecognizer) so
these tests never load real docling models.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from docolage.ports import FakeVLMRecognizer, LayoutElement

pytestmark = pytest.mark.service

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _small_pdf_bytes() -> bytes:
    return (DATA_DIR / "TSMCJ.PDF").read_bytes()  # 3 pages


def _fake_recognizer() -> FakeVLMRecognizer:
    elements_by_page = {
        page: [LayoutElement(type="text", content=f"page {page} text", confidence=0.99, coord=[0, 0, 10, 10])]
        for page in (1, 2, 3)
    }
    return FakeVLMRecognizer(elements_by_page)


async def _submit_job(client) -> str:
    files = {"file": ("TSMCJ.PDF", _small_pdf_bytes(), "application/pdf")}
    response = await client.post("/submit", files=files, data={"delivery": "async"})
    assert response.status_code == 200
    return response.json()["job_id"]


@pytest.mark.asyncio
async def test_worker_processes_all_pages_and_completes_job(client, service_app) -> None:
    from docolage_service.worker.main import _SourceDocCache, claim_and_process_one

    job_id = await _submit_job(client)

    pool = service_app.state.pg_pool
    object_store = service_app.state.object_store
    recognizer = _fake_recognizer()

    with tempfile.TemporaryDirectory() as tmpdir:
        source_cache = _SourceDocCache(object_store, Path(tmpdir))
        # 3 pages to claim, plus one extra call that should find nothing left.
        for _ in range(4):
            await claim_and_process_one(
                pool, object_store, recognizer, source_cache=source_cache, lease_seconds=300
            )

    async with pool.acquire() as conn:
        results = await conn.fetch(
            "SELECT page_number, markdown, elements FROM page_results WHERE job_id = $1::uuid ORDER BY page_number",
            job_id,
        )
        job_status = await conn.fetchval("SELECT status FROM jobs WHERE id = $1::uuid", job_id)
        page_statuses = await conn.fetch(
            "SELECT status FROM page_jobs WHERE job_id = $1::uuid", job_id
        )

    assert len(results) == 3
    for row in results:
        assert row["markdown"]
        assert row["elements"] is not None

    assert job_status == "completed"
    assert all(row["status"] == "succeeded" for row in page_statuses)


@pytest.mark.asyncio
async def test_concurrent_claims_never_claim_the_same_page_twice(client, service_app) -> None:
    from docolage_service.worker.main import _SourceDocCache, claim_and_process_one

    job_id = await _submit_job(client)

    pool = service_app.state.pg_pool
    object_store = service_app.state.object_store

    with tempfile.TemporaryDirectory() as tmpdir_a, tempfile.TemporaryDirectory() as tmpdir_b:
        cache_a = _SourceDocCache(object_store, Path(tmpdir_a))
        cache_b = _SourceDocCache(object_store, Path(tmpdir_b))
        recognizer_a = _fake_recognizer()
        recognizer_b = _fake_recognizer()

        async def _drain(recognizer, source_cache) -> int:
            claimed_count = 0
            for _ in range(3):
                claimed = await claim_and_process_one(
                    pool, object_store, recognizer, source_cache=source_cache, lease_seconds=300
                )
                if claimed:
                    claimed_count += 1
            return claimed_count

        results = await asyncio.gather(
            _drain(recognizer_a, cache_a),
            _drain(recognizer_b, cache_b),
        )

    assert sum(results) == 3

    # Each Recognizer only ever saw the pages *it* claimed, and the union
    # of both is exactly {1, 2, 3} with no page seen twice.
    pages_seen = sorted(call[1] for call in recognizer_a.calls) + sorted(call[1] for call in recognizer_b.calls)
    assert sorted(pages_seen) == [1, 2, 3]

    async with pool.acquire() as conn:
        page_statuses = await conn.fetch(
            "SELECT page_number, status FROM page_jobs WHERE job_id = $1::uuid ORDER BY page_number", job_id
        )

    assert [row["status"] for row in page_statuses] == ["succeeded", "succeeded", "succeeded"]
