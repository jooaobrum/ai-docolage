"""Thin Python wrapper around the docolage_service job API.

Mirrors `api/routes/jobs.py` one-to-one: each function makes a single HTTP
call against a running service instance and returns the parsed JSON body.
No CLI, no direct DB access — just functions for callers who'd otherwise
have to hand-roll httpx requests against the endpoints.
"""

from pathlib import Path
from typing import Any

import httpx

__all__ = [
    "DocolageAPIError",
    "DocolageClient",
]


class DocolageAPIError(Exception):
    """Raised when the service responds with a non-2xx status code."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")


class DocolageClient:
    """Calls the docolage_service job endpoints over HTTP.

    Example:
        client = DocolageClient("http://localhost:8000")
        job = client.execute_job("report.pdf")
        client.get_job(job["job_id"])
        client.list_jobs(status="running")
        client.cancel_job(job["job_id"])
        client.retry_job(job["job_id"])
        client.get_job_result(job["job_id"], format="markdown")
    """

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "DocolageClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        response = self._http.request(method, path, **kwargs)
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise DocolageAPIError(response.status_code, detail)
        return response.json()

    def execute_job(self, file_path: str | Path, *, delivery: str = "async") -> dict:
        """POST /submit — upload a document and create a job."""
        file_path = Path(file_path)
        with file_path.open("rb") as f:
            files = {"file": (file_path.name, f)}
            data = {"delivery": delivery}
            return self._request("POST", "/submit", files=files, data=data)

    def get_job(self, job_id: str) -> dict:
        """GET /jobs/{job_id} — fetch a job's current status."""
        return self._request("GET", f"/jobs/{job_id}")

    def list_jobs(
        self,
        *,
        status: str | None = None,
        stale: bool = False,
        stale_after_seconds: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """GET /jobs — list jobs, optionally filtered by status/staleness."""
        params: dict[str, Any] = {"stale": stale, "limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        if stale_after_seconds is not None:
            params["stale_after_seconds"] = stale_after_seconds
        return self._request("GET", "/jobs", params=params)

    def cancel_job(self, job_id: str) -> dict:
        """POST /jobs/{job_id}/cancel — cancel a job."""
        return self._request("POST", f"/jobs/{job_id}/cancel")

    def retry_job(self, job_id: str) -> dict:
        """POST /jobs/{job_id}/retry — retry a failed or partial job."""
        return self._request("POST", f"/jobs/{job_id}/retry")

    def get_job_result(self, job_id: str, *, format: str = "markdown") -> dict:
        """GET /jobs/{job_id}/result — fetch a terminal job's result."""
        return self._request("GET", f"/jobs/{job_id}/result", params={"format": format})
