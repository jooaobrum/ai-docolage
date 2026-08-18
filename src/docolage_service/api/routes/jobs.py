"""POST /submit and GET /jobs/{job_id} — ticket #20.

Per spec "API contract", submit performs, in order: (a) validate format
and size, (b) write the upload to object storage, (c) read the page
count, (d) insert the job and its page jobs in one transaction.
Rasterization is deliberately not done here — a Worker rasterizes its own
page from the source document (spec, "API contract").

This module does not build the sync stream (ticket #23) or the result
endpoint (ticket #22) — only submit + status.
"""

import logging
import uuid
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from docolage_service.api.rate_limit import limiter
from docolage_service.db import queries
from docolage_service.jobs import reassembly
from docolage_service.metrics import JOBS_SUBMITTED_TOTAL

logger = logging.getLogger(__name__)

router = APIRouter()

# Kept in sync with docolage.parser's registered extensions (Core's
# _HANDLERS: .pdf, .png, .docx, .pptx, .md, .markdown) — spec user story
# 15 requires the rejection error to name the supported formats, so this
# list is the single source of truth for that message.
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".docx", ".pptx", ".md", ".markdown"}
_OFFICE_EXTENSIONS = {".docx", ".pptx"}
_SINGLE_PAGE_EXTENSIONS = {".png", ".md", ".markdown"}

class SubmitResponse(BaseModel):
    job_id: str
    status: str

class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    pages_done: int
    pages_total: int
    delivery: str
    error: str | None = None

def _extension(filename: str) -> str:
    return Path(filename).suffix.lower()

def _count_pdf_pages(pdf_bytes: bytes, original_filename: str) -> int:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
        return len(pdf)
    except Exception as exc:  # noqa: BLE001 - any pdfium failure means "unreadable"
        raise HTTPException(
            status_code=422,
            detail=(
                f"Could not read {original_filename!r}: the PDF appears corrupt or "
                "unreadable (page count could not be determined)."
            ),
        ) from exc

def _convert_office_to_pdf_and_count_pages(raw_bytes: bytes, filename: str, timeout_seconds: int) -> int:
    from docolage.exceptions import DocumentConversionError, LibreOfficeNotFoundError
    from docolage.office_convert import convert_to_pdf

    with TemporaryDirectory() as tmpdir:
        tmp_dir_path = Path(tmpdir)
        source_path = tmp_dir_path / filename
        source_path.write_bytes(raw_bytes)
        try:
            converted_path = convert_to_pdf(source_path, tmp_dir_path, timeout_seconds=timeout_seconds)
        except LibreOfficeNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except DocumentConversionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _count_pdf_pages(converted_path.read_bytes(), filename)

@router.post("/submit", response_model=SubmitResponse)
@limiter.limit("10/minute")
async def submit(
    request: Request,
    file: UploadFile = File(...),
    delivery: str = Form("async"),
) -> SubmitResponse:
    settings = request.app.state.settings
    pool = request.app.state.pg_pool
    object_store = request.app.state.object_store

    if delivery not in ("sync", "async"):
        raise HTTPException(
            status_code=400,
            detail=f"delivery must be 'sync' or 'async', got {delivery!r}.",
        )

    filename = file.filename or "upload"
    extension = _extension(filename)

    # (a) validate format and size, in order, before touching storage.
    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file format {extension!r}. Supported formats: {supported}.",
        )

    raw_bytes = await file.read()
    if len(raw_bytes) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File is {len(raw_bytes)} bytes, which exceeds the configured maximum of "
                f"{settings.max_upload_bytes} bytes."
            ),
        )

    # (b) write the upload to object storage before acknowledging (story 14).
    object_key = f"uploads/{uuid.uuid4()}/{filename}"
    source_uri = await object_store.put(object_key, raw_bytes)

    # (c) determine page count.
    if extension == ".pdf":
        total_pages = _count_pdf_pages(raw_bytes, filename)
    elif extension in _OFFICE_EXTENSIONS:
        total_pages = _convert_office_to_pdf_and_count_pages(
            raw_bytes, filename, settings.office_convert_timeout_seconds
        )
    elif extension in _SINGLE_PAGE_EXTENSIONS:
        total_pages = 1
    else:  # pragma: no cover - unreachable given SUPPORTED_EXTENSIONS check above
        raise HTTPException(status_code=415, detail=f"Unsupported file format {extension!r}.")

    # Sync gate (story 19): a sync submission over the page threshold is
    # refused with an actionable error rather than accepted and starved.
    if delivery == "sync" and total_pages > settings.sync_gate_max_pages:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Document has {total_pages} pages, which exceeds the Sync gate limit of "
                f"{settings.sync_gate_max_pages} pages. Submit with delivery='async' instead."
            ),
        )

    priority = queries.SYNC_PRIORITY if delivery == "sync" else queries.ASYNC_PRIORITY

    # (d) insert the job and its page jobs in one transaction.
    job_id = await queries.insert_job_with_pages(
        pool,
        delivery_mode=delivery,
        priority=priority,
        source_uri=source_uri,
        original_filename=filename,
        format=extension.lstrip("."),
        total_pages=total_pages,
    )

    JOBS_SUBMITTED_TOTAL.labels(delivery=delivery).inc()
    logger.info("submitted job_id=%s delivery=%s pages=%s", job_id, delivery, total_pages, extra={"job_id": job_id})

    return SubmitResponse(job_id=job_id, status="queued")

async def _fetch_job_or_404(pool, job_id: str) -> queries.JobRow:
    try:
        job = await queries.fetch_job(pool, job_id)
    except Exception as exc:  # noqa: BLE001 - malformed UUID -> not-found, not a 500
        raise HTTPException(status_code=404, detail=f"No job found with id {job_id!r}.") from exc

    if job is None:
        raise HTTPException(status_code=404, detail=f"No job found with id {job_id!r}.")
    return job


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, request: Request) -> JobStatusResponse:
    pool = request.app.state.pg_pool

    job = await _fetch_job_or_404(pool, job_id)
    return await _job_status_response(pool, job_id, job)


async def _job_status_response(pool, job_id: str, job: queries.JobRow) -> JobStatusResponse:
    pages_done = await queries.count_pages_done(pool, job_id)
    return JobStatusResponse(
        job_id=str(job.id),
        status=job.status,
        pages_done=pages_done,
        pages_total=job.total_pages,
        delivery=job.delivery_mode,
        error=job.error_summary,
    )


# Every status a job can be in — the CHECK constraint's source of truth on
# the Python side, used to validate ?status= on GET /jobs (spec: reject
# unknown values with an actionable 400, matching the `delivery` validation
# in submit above).
_JOB_STATUSES = {"queued", "running", "completed", "partial", "failed", "cancelled"}

# Jobs a client can meaningfully ask to retry: derive_job_status only
# reaches 'failed' or 'partial' once at least one page has permanently
# failed (docolage_service/jobs/status.py) — retrying anything else (still
# in-flight, or fully completed) is a client error, not a queue operation.
_RETRYABLE_STATUSES = {"failed", "partial"}


@router.post("/jobs/{job_id}/cancel", response_model=JobStatusResponse)
async def cancel_job(job_id: str, request: Request) -> JobStatusResponse:
    pool = request.app.state.pg_pool

    await _fetch_job_or_404(pool, job_id)
    await queries.cancel_job(pool, job_id)

    job = await _fetch_job_or_404(pool, job_id)
    return await _job_status_response(pool, job_id, job)


@router.post("/jobs/{job_id}/retry", response_model=JobStatusResponse)
async def retry_job(job_id: str, request: Request) -> JobStatusResponse:
    pool = request.app.state.pg_pool

    job = await _fetch_job_or_404(pool, job_id)
    if job.status not in _RETRYABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Job {job_id!r} has status {job.status!r}; only jobs with status "
                f"{sorted(_RETRYABLE_STATUSES)} can be retried."
            ),
        )

    await queries.retry_job(pool, job_id)

    job = await _fetch_job_or_404(pool, job_id)
    return await _job_status_response(pool, job_id, job)


class JobSummary(BaseModel):
    job_id: str
    status: str
    delivery: str
    total_pages: int
    created_at: datetime
    updated_at: datetime
    error: str | None = None


class JobListResponse(BaseModel):
    jobs: list[JobSummary]
    limit: int
    offset: int


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    request: Request,
    status: str | None = Query(None, description="Filter by job status"),
    stale: bool = Query(False, description="Only 'running' jobs with no progress for stale_after_seconds"),
    stale_after_seconds: int | None = Query(None, description="Overrides the configured stall timeout"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> JobListResponse:
    if status is not None and status not in _JOB_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of {sorted(_JOB_STATUSES)}, got {status!r}.",
        )

    pool = request.app.state.pg_pool
    rows = await queries.list_jobs(
        pool,
        status=status,
        stale_only=stale,
        stale_after_seconds=stale_after_seconds,
        limit=limit,
        offset=offset,
    )

    return JobListResponse(
        jobs=[
            JobSummary(
                job_id=str(row.id),
                status=row.status,
                delivery=row.delivery_mode,
                total_pages=row.total_pages,
                created_at=row.created_at,
                updated_at=row.updated_at,
                error=row.error_summary,
            )
            for row in rows
        ],
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/result — ticket #22, async result retrieval.
#
# Kept below and separate from submit/status above so a concurrent edit
# adding the sync-stream route (ticket #23) elsewhere in this file is
# unlikely to conflict here.
# ---------------------------------------------------------------------------

# Terminal job states from which a result can be reassembled (spec user
# story 36: a job still queued/running must get a not-ready response, never
# a partial document). "failed" is terminal too (story 58) — its result is
# simply all error_status entries and no successful pages.
_TERMINAL_STATUSES = {"completed", "partial", "failed"}


class MarkdownResultPage(BaseModel):
    page_number: int
    markdown: str


class MarkdownResultResponse(BaseModel):
    """format=markdown: the document's successful pages, in page order,
    each carrying its own rendered markdown (spec user stories 34/35/45)."""

    job_id: str
    status: str
    pages: list[MarkdownResultPage]


class ElementBboxResponse(BaseModel):
    coord: list
    page_id: int


class ElementResponse(BaseModel):
    id: int
    type: str
    content: str
    confidence: float
    description: str = ""
    bbox: list[ElementBboxResponse]
    image_uri: str = ""
    captioned: bool = False


class PageResponse(BaseModel):
    id: int
    image_uri: str = ""
    markdown: str = ""


class DocumentResponse(BaseModel):
    pages: list[PageResponse]
    elements: list[ElementResponse]


class ErrorStatusEntry(BaseModel):
    error_message: str
    page_id: int


class ElementsResultResponse(BaseModel):
    """format=elements: the full ai_parse_document-shaped result (ADR-0002),
    mirroring docolage.schema.build_document — plus job_id/status so a
    caller can tell which job and terminal state produced it."""

    job_id: str
    status: str
    document: DocumentResponse
    error_status: list[ErrorStatusEntry]
    metadata: dict


@router.get("/jobs/{job_id}/result")
async def get_job_result(
    job_id: str,
    request: Request,
    format: str = Query("markdown", description="'markdown' or 'elements'"),
) -> MarkdownResultResponse | ElementsResultResponse:
    if format not in (reassembly.MARKDOWN_FORMAT, reassembly.ELEMENTS_FORMAT):
        raise HTTPException(
            status_code=400,
            detail=f"format must be 'markdown' or 'elements', got {format!r}.",
        )

    pool = request.app.state.pg_pool
    job = await _fetch_job_or_404(pool, job_id)

    if job.status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Job {job_id!r} is not ready (status={job.status!r}). "
                "Poll GET /jobs/{job_id} until status is 'completed', 'partial' or 'failed'."
            ),
        )

    page_results = await queries.fetch_page_results(pool, job_id)
    page_jobs = await queries.fetch_page_jobs(pool, job_id)
    result = reassembly.reassemble(page_results, page_jobs, format)  # type: ignore[arg-type]

    if format == reassembly.MARKDOWN_FORMAT:
        return MarkdownResultResponse(job_id=str(job.id), status=job.status, pages=result["pages"])

    return ElementsResultResponse(
        job_id=str(job.id),
        status=job.status,
        document=result["document"],
        error_status=result["error_status"],
        metadata=result["metadata"],
    )
