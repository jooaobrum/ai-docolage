"""GET /jobs/{job_id}/stream — ticket #23, the Sync path over SSE.

Per ADR-0008 ("Sync delivery"), this is implemented by **polling the
page-result table** on a short interval and emitting newly-completed
pages in page order, tracking a high-water mark per stream — no
LISTEN/NOTIFY, no broker. Postgres remains the single source of truth,
so a reconnecting client (or a fresh poll after a gap) never needs to
backfill from anywhere else.

Kept as its own module/router (rather than added to routes/jobs.py) so
this ticket does not collide with ticket #22's concurrent edits to that
file — see api/app.py for registration.

Event shapes (hand-rolled `text/event-stream` framing, no sse-starlette
dependency — none was already present, and plain `data: ...\\n\\n` chunks
are enough):

- `{"type": "page", "page_number": N, "markdown": "..."}` — one
  successfully parsed page (spec user story 24).
- `{"type": "page_error", "page_number": N, "error": "..."}` — a page
  that reached a permanently-failed page_job state (spec user story 27).
- `{"type": "complete", "status": "completed" | "partial" | "failed"}` —
  terminal event once the job itself reaches a terminal status (spec
  user story 26), then the stream closes.
- `{"type": "timeout"}` — the wall-clock limit
  (`settings.sync_stream_timeout_seconds`) elapsed before the job
  finished (spec user story 28); the job itself is left running.

Client disconnect (story 29) is handled for free: Starlette stops
iterating the generator once the client goes away, and the generator
here touches no job/page_job state on exit — only its own local
high-water mark — so nothing about the underlying job changes.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from docolage_service.api.routes.jobs import _fetch_job_or_404
from docolage_service.db import queries
from docolage_service.jobs.stream_ordering import next_contiguous_pages

router = APIRouter()

_TERMINAL_JOB_STATUSES = {"completed", "partial", "failed"}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _stream_job_pages(request: Request, job_id: str) -> AsyncIterator[str]:
    settings = request.app.state.settings
    pool = request.app.state.pg_pool

    high_water_mark = 0
    pending_by_page: dict[int, dict] = {}
    deadline = time.monotonic() + settings.sync_stream_timeout_seconds

    while True:
        job = await queries.fetch_job(pool, job_id)
        if job is None:
            # The job vanished between the initial existence check in the
            # route and here (shouldn't happen in practice — jobs are
            # never deleted by this service — but fail closed rather than
            # loop forever).
            return

        outcomes = await queries.fetch_page_outcomes_since(pool, job_id, high_water_mark)
        for outcome in outcomes:
            if outcome.error is not None:
                pending_by_page[outcome.page_number] = {
                    "type": "page_error",
                    "page_number": outcome.page_number,
                    "error": outcome.error,
                }
            else:
                pending_by_page[outcome.page_number] = {
                    "type": "page",
                    "page_number": outcome.page_number,
                    "markdown": outcome.markdown,
                }

        ready_pages = next_contiguous_pages(high_water_mark, list(pending_by_page.keys()))
        for page_number in ready_pages:
            yield _sse(pending_by_page.pop(page_number))
            high_water_mark = page_number

        if job.status in _TERMINAL_JOB_STATUSES and not pending_by_page:
            # Every contiguous page up to the high-water mark has been
            # emitted and the job itself is done — safe to close.
            yield _sse({"type": "complete", "status": job.status})
            return

        if time.monotonic() >= deadline:
            yield _sse({"type": "timeout"})
            return

        await asyncio.sleep(settings.sse_poll_interval_seconds)


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request) -> StreamingResponse:
    pool = request.app.state.pg_pool
    await _fetch_job_or_404(pool, job_id)

    return StreamingResponse(
        _stream_job_pages(request, job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
