"""DocolageClient makes the right HTTP calls and surfaces errors as DocolageAPIError.

Uses httpx.MockTransport instead of a real service instance (unlike
tests/service/test_jobs.py, which needs real Postgres/object storage) —
this module only needs to verify the client builds correct requests and
parses responses, not end-to-end job processing.
"""

from __future__ import annotations

import httpx
import pytest

from docolage_service.client import DocolageAPIError, DocolageClient


def _client_with(handler) -> DocolageClient:
    client = DocolageClient("http://testserver")
    client._http = httpx.Client(base_url="http://testserver", transport=httpx.MockTransport(handler))
    return client


def test_execute_job_posts_file_and_delivery(tmp_path) -> None:
    file_path = tmp_path / "doc.pdf"
    file_path.write_bytes(b"%PDF-1.4 fake")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/submit"
        assert b'name="delivery"' in request.content
        assert b"doc.pdf" in request.content
        return httpx.Response(200, json={"job_id": "abc", "status": "queued"})

    client = _client_with(handler)
    result = client.execute_job(file_path, delivery="sync")
    assert result == {"job_id": "abc", "status": "queued"}


def test_get_job_hits_correct_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/jobs/abc"
        return httpx.Response(200, json={"job_id": "abc", "status": "running"})

    client = _client_with(handler)
    assert client.get_job("abc")["status"] == "running"


def test_list_jobs_passes_filters_as_params() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/jobs"
        params = dict(request.url.params)
        assert params["status"] == "failed"
        assert params["limit"] == "10"
        assert params["offset"] == "5"
        return httpx.Response(200, json={"jobs": [], "limit": 10, "offset": 5})

    client = _client_with(handler)
    result = client.list_jobs(status="failed", limit=10, offset=5)
    assert result == {"jobs": [], "limit": 10, "offset": 5}


def test_cancel_job_posts_to_cancel_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/jobs/abc/cancel"
        return httpx.Response(200, json={"job_id": "abc", "status": "cancelled"})

    client = _client_with(handler)
    assert client.cancel_job("abc")["status"] == "cancelled"


def test_retry_job_posts_to_retry_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/jobs/abc/retry"
        return httpx.Response(200, json={"job_id": "abc", "status": "queued"})

    client = _client_with(handler)
    assert client.retry_job("abc")["status"] == "queued"


def test_get_job_result_passes_format_param() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/jobs/abc/result"
        assert dict(request.url.params)["format"] == "elements"
        return httpx.Response(200, json={"job_id": "abc", "status": "completed"})

    client = _client_with(handler)
    result = client.get_job_result("abc", format="elements")
    assert result["status"] == "completed"


def test_error_response_raises_docolage_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "No job found with id 'abc'."})

    client = _client_with(handler)
    with pytest.raises(DocolageAPIError) as exc_info:
        client.get_job("abc")

    assert exc_info.value.status_code == 404
    assert "No job found" in exc_info.value.detail
