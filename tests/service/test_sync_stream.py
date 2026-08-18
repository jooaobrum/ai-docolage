"""HTTP-observable behaviour of GET /jobs/{job_id}/stream (ticket #23).

Per spec user stories 23-30 and ADR-0008: drives the API through an
in-process ASGI client against real throwaway Postgres + object storage,
driving the worker's `claim_and_process_one` directly (same harness as
test_worker.py) to complete pages, then asserts only on what the SSE
client observes — event order, event shapes, and job status afterward —
never on table contents directly.
"""

from __future__ import annotations

import asyncio
import json
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


async def _submit_job(client, delivery: str = "sync") -> str:
    files = {"file": ("TSMCJ.PDF", _small_pdf_bytes(), "application/pdf")}
    response = await client.post("/submit", files=files, data={"delivery": delivery})
    assert response.status_code == 200
    return response.json()["job_id"]


async def _drain_worker(pool, object_store, source_cache, recognizer, n: int) -> None:
    """Claim and process exactly n page_jobs, in claim order (which is
    priority/created_at order, not necessarily page-number order)."""
    from docolage_service.worker.main import claim_and_process_one

    for _ in range(n):
        claimed = await claim_and_process_one(
            pool, object_store, recognizer, source_cache=source_cache, lease_seconds=300
        )
        assert claimed


async def _collect_stream_events(client, job_id: str) -> list[dict]:
    events: list[dict] = []
    async with client.stream("GET", f"/jobs/{job_id}/stream") as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            events.append(json.loads(line[len("data: "):]))
            if events[-1]["type"] in ("complete", "timeout"):
                break
    return events


@pytest.mark.asyncio
async def test_sync_stream_emits_pages_in_order_then_completes(client, service_app) -> None:
    job_id = await _submit_job(client, delivery="sync")

    pool = service_app.state.pg_pool
    object_store = service_app.state.object_store

    from docolage_service.worker.main import _SourceDocCache

    with tempfile.TemporaryDirectory() as tmpdir:
        source_cache = _SourceDocCache(object_store, Path(tmpdir))
        recognizer = _fake_recognizer()

        # Process all 3 pages before opening the stream, so we exercise
        # the "already-complete job" path deterministically (claim order
        # is priority/created_at, not page order — this still proves
        # story 25 because the stream must emit 1, 2, 3 regardless of
        # what order the underlying page_jobs actually finished in).
        await _drain_worker(pool, object_store, source_cache, recognizer, n=3)

    events = await _collect_stream_events(client, job_id)

    page_events = [e for e in events if e["type"] == "page"]
    assert [e["page_number"] for e in page_events] == [1, 2, 3]
    for e in page_events:
        assert e["markdown"]

    assert events[-1] == {"type": "complete", "status": "completed"}

    # story 30: same job id retrievable through the async status endpoint.
    status_response = await client.get(f"/jobs/{job_id}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_sync_stream_emits_pages_as_they_complete_concurrently(client, service_app) -> None:
    """Open the stream first, then complete pages out of order in the
    background (page 2 before page 1) — asserts the stream withholds page
    2 until page 1 lands, then emits both in order (spec story 25)."""
    job_id = await _submit_job(client, delivery="sync")

    pool = service_app.state.pg_pool

    from docolage_service.db import queries

    recognizer = _fake_recognizer()

    async def _complete_out_of_order() -> None:
        # Claim all 3 (order is deterministic: created_at ASC, i.e.
        # page 1, 2, 3), but persist page 2's result before page 1's to
        # force an out-of-order arrival at the page_results table.
        claims = []
        for _ in range(3):
            claimed = await queries.claim_next_page_job(pool, lease_seconds=300)
            assert claimed is not None
            claims.append(claimed)

        claims.sort(key=lambda c: c.page_number)  # [page1, page2, page3]
        order = [claims[1], claims[0], claims[2]]  # page2, page1, page3

        for claimed in order:
            await asyncio.sleep(0.1)

            # recognizer is fake and keys purely on page_number; the
            # image path argument is unused, so any placeholder works.
            result = recognizer.recognize("unused.png", claimed.page_number)
            await queries.complete_page_job(
                pool,
                page_job_id=claimed.id,
                job_id=claimed.job_id,
                page_number=claimed.page_number,
                markdown=result.markdown,
                elements=json.dumps([]),
            )

    producer_task = asyncio.create_task(_complete_out_of_order())
    try:
        events = await _collect_stream_events(client, job_id)
    finally:
        await producer_task

    page_events = [e for e in events if e["type"] == "page"]
    assert [e["page_number"] for e in page_events] == [1, 2, 3]
    assert events[-1] == {"type": "complete", "status": "completed"}


@pytest.mark.asyncio
async def test_sync_stream_surfaces_permanently_failed_page(client, service_app) -> None:
    job_id = await _submit_job(client, delivery="sync")

    pool = service_app.state.pg_pool
    object_store = service_app.state.object_store

    from docolage_service.worker.main import _SourceDocCache

    with tempfile.TemporaryDirectory() as tmpdir:
        source_cache = _SourceDocCache(object_store, Path(tmpdir))

        class _FailingRecognizer(FakeVLMRecognizer):
            def recognize(self, image_path, page_number):  # type: ignore[override]
                if page_number == 2:
                    raise RuntimeError("simulated permanent failure")
                return super().recognize(image_path, page_number)

        recognizer = _FailingRecognizer(
            {p: [LayoutElement(type="text", content=f"p{p}", confidence=0.9, coord=[0, 0, 1, 1])] for p in (1, 2, 3)}
        )
        # page 2 requeues on each failure (ticket #24 retries, default
        # page_job_max_attempts=3), so it takes 3 claims of page 2 plus one
        # each for pages 1 and 3 to reach a permanently-failed page 2.
        await _drain_worker(pool, object_store, source_cache, recognizer, n=5)

    events = await _collect_stream_events(client, job_id)

    by_type = {(e["type"], e.get("page_number")): e for e in events}
    assert ("page", 1) in by_type
    assert ("page_error", 2) in by_type
    assert by_type[("page_error", 2)]["error"]
    assert ("page", 3) in by_type
    assert events[-1]["type"] == "complete"
    assert events[-1]["status"] == "partial"


@pytest.mark.asyncio
async def test_disconnecting_stream_does_not_cancel_the_job(client, service_app) -> None:
    """story 29: closing the SSE connection early must not affect job
    status — checked afterward through the plain status endpoint."""
    job_id = await _submit_job(client, delivery="sync")

    # Open the stream and close it immediately without draining it, before
    # any page has completed.
    async with client.stream("GET", f"/jobs/{job_id}/stream") as response:
        assert response.status_code == 200
        async for _line in response.aiter_lines():
            break  # read at most one chunk, then let the context manager close it

    # The job must still be reachable and in a live (non-cancelled) state.
    status_response = await client.get(f"/jobs/{job_id}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] in ("queued", "running")


@pytest.mark.asyncio
async def test_stream_unknown_job_id_returns_404(client) -> None:
    response = await client.get("/jobs/00000000-0000-0000-0000-000000000000/stream")
    assert response.status_code == 404
