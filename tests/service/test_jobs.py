"""HTTP-observable behaviour of POST /submit and GET /jobs/{job_id}.

Per spec user stories 12-22, 31-33, 37 and Testing Decisions "Seam 2 —
the Service": drives the API through an in-process ASGI client against
real throwaway Postgres + object storage, asserting only on what an HTTP
client observes (status codes, response bodies) — never on table
contents directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.service

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

def _small_pdf_bytes() -> bytes:
    return (DATA_DIR / "TSMCJ.PDF").read_bytes()  # 3 pages

def _large_pdf_bytes() -> bytes:
    return (DATA_DIR / "stmicro-financial-report.pdf").read_bytes()  # 469 pages

@pytest.mark.asyncio
async def test_submit_async_creates_job_with_correct_page_count(client) -> None:
    files = {"file": ("TSMCJ.PDF", _small_pdf_bytes(), "application/pdf")}
    response = await client.post("/submit", files=files, data={"delivery": "async"})

    assert response.status_code == 200
    body = response.json()
    assert "job_id" in body
    assert body["status"] == "queued"

    status_response = await client.get(f"/jobs/{body['job_id']}")
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["pages_total"] == 3
    assert status_body["pages_done"] == 0
    assert status_body["delivery"] == "async"
    assert status_body["status"] == "queued"

@pytest.mark.asyncio
async def test_submit_rejects_unsupported_format(client) -> None:
    files = {"file": ("evil.exe", b"not a real file", "application/octet-stream")}
    response = await client.post("/submit", files=files, data={"delivery": "async"})

    assert response.status_code == 415
    assert "unsupported" in response.json()["detail"].lower()

@pytest.mark.asyncio
async def test_submit_rejects_oversized_file(client, service_app) -> None:
    service_app.state.settings.max_upload_bytes = 100
    files = {"file": ("TSMCJ.PDF", _small_pdf_bytes(), "application/pdf")}
    response = await client.post("/submit", files=files, data={"delivery": "async"})

    assert response.status_code == 413

@pytest.mark.asyncio
async def test_submit_rejects_corrupt_pdf(client) -> None:
    files = {"file": ("broken.pdf", b"%PDF-1.4 not actually a valid pdf body", "application/pdf")}
    response = await client.post("/submit", files=files, data={"delivery": "async"})

    assert response.status_code == 422

@pytest.mark.asyncio
async def test_sync_submission_over_sync_gate_is_refused(client, service_app) -> None:
    service_app.state.settings.sync_gate_max_pages = 10
    files = {"file": ("stmicro-financial-report.pdf", _large_pdf_bytes(), "application/pdf")}
    response = await client.post("/submit", files=files, data={"delivery": "sync"})

    assert response.status_code == 413
    detail = response.json()["detail"].lower()
    assert "sync" in detail or "async" in detail

@pytest.mark.asyncio
async def test_get_unknown_job_id_returns_404(client) -> None:
    response = await client.get("/jobs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_get_malformed_job_id_returns_404(client) -> None:
    response = await client.get("/jobs/not-a-uuid")
    assert response.status_code == 404


# --- GET /jobs/{job_id}/result — ticket #22 ---------------------------------


@pytest.mark.asyncio
async def test_result_not_ready_before_any_pages_processed(client) -> None:
    files = {"file": ("TSMCJ.PDF", _small_pdf_bytes(), "application/pdf")}
    submit_response = await client.post("/submit", files=files, data={"delivery": "async"})
    job_id = submit_response.json()["job_id"]

    response = await client.get(f"/jobs/{job_id}/result", params={"format": "markdown"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_result_unknown_job_id_returns_404(client) -> None:
    response = await client.get(
        "/jobs/00000000-0000-0000-0000-000000000000/result", params={"format": "markdown"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_result_markdown_and_elements_after_worker_completes_all_pages(client, service_app) -> None:
    from docolage.ports import FakeVLMRecognizer, LayoutElement
    from docolage_service.worker.main import _SourceDocCache, claim_and_process_one
    import tempfile
    from pathlib import Path

    files = {"file": ("TSMCJ.PDF", _small_pdf_bytes(), "application/pdf")}
    submit_response = await client.post("/submit", files=files, data={"delivery": "async"})
    job_id = submit_response.json()["job_id"]

    elements_by_page = {
        page: [LayoutElement(type="text", content=f"page {page} text", confidence=0.99, coord=[0, 0, 10, 10])]
        for page in (1, 2, 3)
    }
    recognizer = FakeVLMRecognizer(elements_by_page)

    pool = service_app.state.pg_pool
    object_store = service_app.state.object_store
    with tempfile.TemporaryDirectory() as tmpdir:
        source_cache = _SourceDocCache(object_store, Path(tmpdir))
        for _ in range(4):  # 3 pages + one call that finds nothing left
            await claim_and_process_one(
                pool, object_store, recognizer, source_cache=source_cache, lease_seconds=300
            )

    status_response = await client.get(f"/jobs/{job_id}")
    assert status_response.json()["status"] == "completed"

    markdown_response = await client.get(f"/jobs/{job_id}/result", params={"format": "markdown"})
    assert markdown_response.status_code == 200
    markdown_body = markdown_response.json()
    assert [p["page_number"] for p in markdown_body["pages"]] == [1, 2, 3]
    assert all(p["markdown"] for p in markdown_body["pages"])

    elements_response = await client.get(f"/jobs/{job_id}/result", params={"format": "elements"})
    assert elements_response.status_code == 200
    elements_body = elements_response.json()
    assert [p["id"] for p in elements_body["document"]["pages"]] == [1, 2, 3]
    assert len(elements_body["document"]["elements"]) == 3
    assert elements_body["error_status"] == []
    assert "id" in elements_body["metadata"]
