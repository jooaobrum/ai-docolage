"""Retries, lease recovery, and partial jobs (ticket #24).

Same harness as test_worker.py: drives `claim_and_process_one` directly
against real throwaway Postgres + object storage, with the Core's
Recognizer faked. `FakeVLMRecognizer` doesn't support injected
failures per call count out of the box, so a couple of tests below use a
tiny wrapper recognizer to make a page fail N times before succeeding,
or fail permanently.
"""

from __future__ import annotations

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


class _FlakyRecognizer:
    """Wraps a real recognizer; a configured set of pages raise for their
    first `fail_times` calls, then delegate to the wrapped recognizer."""

    def __init__(self, inner: FakeVLMRecognizer, fail_times: dict[int, int]) -> None:
        self._inner = inner
        self._fail_times = dict(fail_times)
        self._attempts: dict[int, int] = {}

    def recognize(self, image_path, page_number):
        self._attempts[page_number] = self._attempts.get(page_number, 0) + 1
        remaining = self._fail_times.get(page_number, 0)
        if remaining > 0 and self._attempts[page_number] <= remaining:
            raise RuntimeError(f"transient failure on page {page_number}, attempt {self._attempts[page_number]}")
        return self._inner.recognize(image_path, page_number)


async def _submit_job(client, filename: str = "TSMCJ.PDF") -> str:
    files = {"file": (filename, _small_pdf_bytes(), "application/pdf")}
    response = await client.post("/submit", files=files, data={"delivery": "async"})
    assert response.status_code == 200
    return response.json()["job_id"]


@pytest.mark.asyncio
async def test_page_that_fails_then_succeeds_within_retry_limit_completes(client, service_app) -> None:
    from docolage_service.worker.main import _SourceDocCache, claim_and_process_one

    job_id = await _submit_job(client)
    pool = service_app.state.pg_pool
    object_store = service_app.state.object_store

    # Page 2 fails on its first attempt, then succeeds on attempt 2 —
    # well within the default max_attempts=3.
    recognizer = _FlakyRecognizer(_fake_recognizer(), fail_times={2: 1})

    with tempfile.TemporaryDirectory() as tmpdir:
        source_cache = _SourceDocCache(object_store, Path(tmpdir))
        # 3 pages + 1 retry for page 2 + 1 extra "nothing left" call.
        for _ in range(6):
            await claim_and_process_one(
                pool, object_store, recognizer, source_cache=source_cache, lease_seconds=300, max_attempts=3
            )

    async with pool.acquire() as conn:
        job_status = await conn.fetchval("SELECT status FROM jobs WHERE id = $1::uuid", job_id)
        page2 = await conn.fetchrow(
            "SELECT status, attempt_count, error_message FROM page_jobs WHERE job_id = $1::uuid AND page_number = 2",
            job_id,
        )

    assert job_status == "completed"
    assert page2["status"] == "succeeded"
    assert page2["attempt_count"] == 2


@pytest.mark.asyncio
async def test_page_exhausting_retries_permanently_fails_and_job_is_partial(client, service_app) -> None:
    from docolage_service.worker.main import _SourceDocCache, claim_and_process_one

    job_id = await _submit_job(client)
    pool = service_app.state.pg_pool
    object_store = service_app.state.object_store

    max_attempts = 3
    # Page 2 always fails -> exhausts all 3 attempts; pages 1 and 3 succeed.
    recognizer = _FlakyRecognizer(_fake_recognizer(), fail_times={2: 999})

    with tempfile.TemporaryDirectory() as tmpdir:
        source_cache = _SourceDocCache(object_store, Path(tmpdir))
        # Enough claim calls for: page1, page3 succeed once each; page2
        # gets claimed and fails max_attempts times; plus a drain call.
        for _ in range(10):
            await claim_and_process_one(
                pool, object_store, recognizer, source_cache=source_cache, lease_seconds=300, max_attempts=max_attempts
            )

    async with pool.acquire() as conn:
        job_status = await conn.fetchval("SELECT status FROM jobs WHERE id = $1::uuid", job_id)
        page2 = await conn.fetchrow(
            "SELECT status, attempt_count, error_message FROM page_jobs WHERE job_id = $1::uuid AND page_number = 2",
            job_id,
        )
        others = await conn.fetch(
            "SELECT status FROM page_jobs WHERE job_id = $1::uuid AND page_number != 2", job_id
        )

    assert page2["status"] == "failed"
    assert page2["attempt_count"] == max_attempts
    assert page2["error_message"]
    assert all(row["status"] == "succeeded" for row in others)
    assert job_status == "partial"

    # The result endpoint should surface the partial job's successful
    # pages plus an error entry for the permanently-failed one (spec
    # user story 57), and be reachable now that the job is terminal.
    response = await client.get(f"/jobs/{job_id}/result", params={"format": "elements"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert len(body["error_status"]) == 1
    assert body["error_status"][0]["page_id"] == 2


@pytest.mark.asyncio
async def test_job_with_every_page_permanently_failed_is_failed(client, service_app) -> None:
    from docolage_service.worker.main import _SourceDocCache, claim_and_process_one

    job_id = await _submit_job(client)
    pool = service_app.state.pg_pool
    object_store = service_app.state.object_store

    max_attempts = 2
    recognizer = _FlakyRecognizer(_fake_recognizer(), fail_times={1: 999, 2: 999, 3: 999})

    with tempfile.TemporaryDirectory() as tmpdir:
        source_cache = _SourceDocCache(object_store, Path(tmpdir))
        for _ in range(10):
            await claim_and_process_one(
                pool, object_store, recognizer, source_cache=source_cache, lease_seconds=300, max_attempts=max_attempts
            )

    async with pool.acquire() as conn:
        job_status = await conn.fetchval("SELECT status FROM jobs WHERE id = $1::uuid", job_id)
        page_statuses = await conn.fetch("SELECT status FROM page_jobs WHERE job_id = $1::uuid", job_id)

    assert all(row["status"] == "failed" for row in page_statuses)
    assert job_status == "failed"

    response = await client.get(f"/jobs/{job_id}/result", params={"format": "markdown"})
    assert response.status_code == 200
    assert response.json()["status"] == "failed"


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimable_and_bumps_attempt_count(client, service_app) -> None:
    from docolage_service.db import queries
    from docolage_service.worker.main import _SourceDocCache, claim_and_process_one

    job_id = await _submit_job(client)
    pool = service_app.state.pg_pool
    object_store = service_app.state.object_store

    # Simulate a Worker crash: claim page 1 with a short lease and never
    # complete or fail it.
    claimed = await queries.claim_next_page_job(pool, lease_seconds=300)
    assert claimed is not None
    assert claimed.attempt_count == 1

    # Backdate the lease instead of sleeping for real.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE page_jobs SET lease_expires_at = now() - interval '1 second' WHERE id = $1::uuid",
            claimed.id,
        )
        still_claimed_status = await conn.fetchval(
            "SELECT status FROM page_jobs WHERE id = $1::uuid", claimed.id
        )
    assert still_claimed_status == "claimed"

    # A fresh claim call should now reclaim exactly this row (only one
    # page_job exists at this point since the other two are still
    # 'queued' with lower... actually all pages start 'queued'; the
    # expired-lease row and the two still-queued rows are all eligible,
    # ordered by priority/created_at. Reclaim the same page directly by
    # draining until we see this page_job id again.
    reclaimed = None
    recognizer = _fake_recognizer()
    with tempfile.TemporaryDirectory() as tmpdir:
        source_cache = _SourceDocCache(object_store, Path(tmpdir))
        for _ in range(5):
            result = await queries.claim_next_page_job(pool, lease_seconds=300)
            if result is not None and result.id == claimed.id:
                reclaimed = result
                break
            if result is not None:
                # process other pages so they don't interfere
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE page_jobs SET status = 'succeeded', updated_at = now() WHERE id = $1::uuid",
                        result.id,
                    )

    assert reclaimed is not None, "expired-lease page_job was not reclaimed"
    assert reclaimed.attempt_count == 2  # incremented again on reclaim
