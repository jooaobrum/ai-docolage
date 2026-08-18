"""Worker entry point: the claim loop (ticket #21).

Repeatedly claims the highest-priority oldest queued page_job (see
db/queries.py's `claim_next_page_job`, the single atomic `FOR UPDATE
SKIP LOCKED` statement), rasterizes and recognizes just that page, and
persists the result — all without ever loading a whole document's worth
of pages into memory at once (spec, "Worker rasterizes its own page").

Per-job source documents are cached on local disk for the lifetime of
this process so N page claims against the same job download the source
exactly once, not N times.

Retry, lease recovery, partial-job status and the stall-timeout sweep
are ticket #24's job: `claim_and_process_one` retries a transiently
failing page up to `page_job_max_attempts` before marking it
permanently failed (see db/queries.py's `fail_page_job`), the claim SQL
reclaims leases from dead Workers, and `run()`'s loop periodically calls
`queries.fail_stalled_jobs` to fail jobs that have made no progress.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import tempfile
from pathlib import Path

import asyncpg

from docolage import schema
from docolage.captioner_openai import build_openai_captioner
from docolage.captioning import caption_figures
from docolage.config import default_recognizer
from docolage.crop import CROPPABLE_TYPES, crop_element
from docolage.rasterize import rasterize_page
from docolage.recognizer_fallback import FallbackRecognizer
from docolage.recognizer_openai import build_openai_recognizer
from docolage.settings_loader import section
from docolage_service.config import get_settings
from docolage_service.db import queries
from docolage_service.db.pool import create_pool
from docolage_service.logging_config import configure_logging
from docolage_service.metrics import (
    OPENAI_FALLBACK_TOTAL,
    PAGE_JOBS_TOTAL,
    PAGE_PROCESSING_SECONDS,
    QUEUE_DEPTH,
)
from docolage_service.storage.object_store import ObjectStore, ObjectStoreSettings

configure_logging("worker")
logger = logging.getLogger("docolage_service.worker")


class _SourceDocCache:
    """Downloads a job's source document at most once per worker process,
    converting Office formats to PDF once too, and caches the resulting
    pdfium document keyed by job_id."""

    def __init__(self, object_store: ObjectStore, work_dir: Path, office_convert_timeout_seconds: int) -> None:
        self._object_store = object_store
        self._work_dir = work_dir
        self._office_convert_timeout_seconds = office_convert_timeout_seconds
        self._pdf_paths: dict[str, Path] = {}

    async def pdf_path_for(self, job_id: str, source_uri: str, fmt: str) -> Path:
        job_id = str(job_id)
        cached = self._pdf_paths.get(job_id)
        if cached is not None:
            return cached

        key = source_uri.split("/", 3)[-1]  # strip "s3://bucket/"
        raw_bytes = await self._object_store.get(key)

        job_dir = self._work_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        original_path = job_dir / f"source.{fmt}"
        original_path.write_bytes(raw_bytes)

        if fmt in ("docx", "pptx"):
            from docolage.office_convert import convert_to_pdf

            pdf_path = convert_to_pdf(
                original_path, job_dir, timeout_seconds=self._office_convert_timeout_seconds
            )
        else:
            pdf_path = original_path

        self._pdf_paths[job_id] = pdf_path
        return pdf_path

    def forget(self, job_id: str) -> None:
        self._pdf_paths.pop(str(job_id), None)


async def claim_and_process_one(
    pool: asyncpg.Pool,
    object_store: ObjectStore,
    recognizer,
    *,
    source_cache: "_SourceDocCache",
    lease_seconds: int,
    dpi: int = section("recognition")["dpi"],
    figure_captioner=None,
    max_attempts: int = section("queue")["page_job_max_attempts"],
    page_processing_timeout_seconds: float = section("queue")["page_processing_timeout_seconds"],
) -> bool:
    """Claim one page_job and process it end to end. Returns True if a
    page_job was claimed (regardless of success/failure), False if there
    was nothing claimable. Never raises: any failure while processing a
    claimed page is caught and recorded as that page_job's failure so the
    worker process keeps running.

    This is the unit the loop below calls repeatedly, and the same unit
    tests call directly in a bounded loop instead of driving a real
    subprocess (spec Testing Decisions, "Seam 2").
    """
    claimed = await queries.claim_next_page_job(pool, lease_seconds)
    if claimed is None:
        return False

    logger.info(
        "claimed page_job id=%s job_id=%s page_number=%s attempt=%s",
        claimed.id,
        claimed.job_id,
        claimed.page_number,
        claimed.attempt_count,
        extra={"job_id": str(claimed.job_id), "page_job_id": str(claimed.id)},
    )
    await queries.mark_job_running_if_needed(pool, claimed.job_id)

    try:
        import pypdfium2 as pdfium

        pdf_path = await source_cache.pdf_path_for(claimed.job_id, claimed.source_uri, claimed.format)
        page_index = claimed.page_number - 1
        output_dir = source_cache._work_dir / str(claimed.job_id) / "pages"

        def _process() -> tuple[str, list[dict]]:
            pdf = pdfium.PdfDocument(str(pdf_path))
            try:
                image_path = rasterize_page(pdf, page_index, output_dir, pdf_path.stem, dpi)
            finally:
                pdf.close()

            result = recognizer.recognize(image_path, claimed.page_number)
            raw_elements = [
                {
                    "type": el.type,
                    "content": el.content,
                    "confidence": el.confidence,
                    "description": el.description,
                    "bbox": [{"page_id": claimed.page_number, "coord": el.coord}],
                }
                for el in result.elements
            ]

            elements = []
            for i, e in enumerate(raw_elements, start=1):
                image_uri = ""
                if e["type"] in CROPPABLE_TYPES:
                    image_uri = crop_element(
                        image_path, e["bbox"][0]["coord"], output_dir, pdf_path.stem, claimed.page_number, i
                    )
                elements.append(
                    schema.make_element(
                        element_id=i,
                        element_type=e["type"],
                        content=e["content"],
                        confidence=e["confidence"],
                        bbox=e["bbox"],
                        description=e["description"],
                        image_uri=image_uri,
                    )
                )
            elements = caption_figures(elements, figure_captioner)
            return result.markdown, elements

        try:
            with PAGE_PROCESSING_SECONDS.time():
                markdown, elements = await asyncio.wait_for(
                    asyncio.to_thread(_process), timeout=page_processing_timeout_seconds
                )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"page timed out after {page_processing_timeout_seconds}s"
            ) from exc

        PAGE_JOBS_TOTAL.labels(result="succeeded").inc()
        await queries.complete_page_job(
            pool,
            page_job_id=claimed.id,
            job_id=claimed.job_id,
            page_number=claimed.page_number,
            markdown=markdown,
            elements=json.dumps(elements),
        )
        logger.info(
            "succeeded page_job id=%s job_id=%s page_number=%s",
            claimed.id,
            claimed.job_id,
            claimed.page_number,
            extra={"job_id": str(claimed.job_id), "page_job_id": str(claimed.id)},
        )
    except Exception as exc:  # noqa: BLE001 - a failed page must not crash the worker
        PAGE_JOBS_TOTAL.labels(result="failed").inc()
        logger.exception(
            "failed page_job id=%s job_id=%s page_number=%s: %s",
            claimed.id,
            claimed.job_id,
            claimed.page_number,
            exc,
            extra={"job_id": str(claimed.job_id), "page_job_id": str(claimed.id)},
        )
        await queries.fail_page_job(
            pool,
            page_job_id=claimed.id,
            job_id=claimed.job_id,
            error_message=str(exc),
            max_attempts=max_attempts,
        )

    return True


async def run() -> None:
    settings = get_settings()
    stop_event = asyncio.Event()

    from prometheus_client import start_http_server

    start_http_server(settings.worker_metrics_port)
    logger.info("worker metrics exposed on :%s/metrics", settings.worker_metrics_port)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info("worker starting, waiting for postgres at %s", settings.postgres_dsn)
    pool = await create_pool(
        settings.postgres_dsn,
        min_size=settings.postgres_pool_min_size,
        max_size=settings.postgres_pool_max_size,
    )
    object_store = ObjectStore(
        ObjectStoreSettings(
            endpoint_url=settings.object_storage_endpoint_url,
            bucket=settings.object_storage_bucket,
            access_key=settings.object_storage_access_key,
            secret_key=settings.object_storage_secret_key,
            region=settings.object_storage_region,
            use_ssl=settings.object_storage_use_ssl,
        )
    )

    # Recognizer constructed once for the whole process (spec/ticket
    # instruction): default_recognizer() is itself memoized, so calling it
    # here just pays docling's model-load cost once, up front.
    #
    # Wrapped in a FallbackRecognizer (docolage.recognizer_fallback):
    # below settings.recognition_confidence_threshold, a page is
    # re-recognized via OpenAI instead, if OPENAI_API_KEY is configured.
    # build_openai_recognizer() returns None when it isn't, and
    # FallbackRecognizer treats None the same way caption_figures treats a
    # None figure_captioner -- the fallback never runs, no code change
    # needed to disable it.
    #
    # Same ImportError posture as figure captioning below: a key
    # configured without the 'openai' extra installed must not crash the
    # Worker, since recognition (unlike captioning) is not optional for
    # any page.
    try:
        openai_recognizer = build_openai_recognizer(model=settings.openai_model)
    except ImportError:
        logger.exception(
            "OPENAI_API_KEY is configured but the 'openai' package is not "
            "installed in this image -- continuing with the recognition "
            "fallback disabled rather than crashing the Worker. Rebuild "
            "the image with the 'openai' extra to fix (see Dockerfile.service)."
        )
        openai_recognizer = None
    logger.info(
        "recognition_fallback=%s threshold=%s",
        "configured" if openai_recognizer is not None else "none (OPENAI_API_KEY not set)",
        settings.recognition_confidence_threshold,
    )
    recognizer = FallbackRecognizer(
        default_recognizer(),
        openai_recognizer,
        confidence_threshold=settings.recognition_confidence_threshold,
        on_fallback=OPENAI_FALLBACK_TOTAL.inc,
    )

    # Unconditional captioning, per ADR-0010: caption every figure when a
    # key is configured, none when it isn't. build_openai_captioner()
    # resolves OPENAI_API_KEY and returns None when it isn't set —
    # caption_figures() already treats None as "skip captioning", so this
    # degrades cleanly with no key set.
    #
    # It can also raise ImportError if a key IS configured but the
    # 'openai' extra isn't installed in this image — that's a real
    # misconfiguration worth surfacing loudly, but captioning is
    # optional end-to-end (ADR-0010), so it must not take down the
    # entire Worker process (every document, not just figures, would
    # stop being parsed). Log it and continue with no captioner instead.
    try:
        figure_captioner = build_openai_captioner(model=settings.openai_model)
    except ImportError:
        logger.exception(
            "OPENAI_API_KEY is configured but the 'openai' package is not "
            "installed in this image -- continuing with figure captioning "
            "disabled rather than crashing the Worker. Rebuild the image "
            "with the 'openai' extra to fix (see Dockerfile.service)."
        )
        figure_captioner = None
    logger.info(
        "figure_captioner=%s", "configured" if figure_captioner is not None else "none (OPENAI_API_KEY not set)"
    )

    # This process itself always processes one page_job at a time,
    # sequentially (see Settings.worker_page_concurrency's docstring for
    # why); logged here so an operator can see, per Worker, what value it
    # was started with while deciding how many Worker replicas to run per
    # GPU.
    logger.info(
        "worker_page_concurrency=%s (informs Worker replica count per GPU; "
        "this process still claims/processes one page_job at a time)",
        settings.worker_page_concurrency,
    )

    with tempfile.TemporaryDirectory(prefix="docolage-worker-") as tmpdir:
        source_cache = _SourceDocCache(object_store, Path(tmpdir), settings.office_convert_timeout_seconds)

        # Wedged-job sweep (ticket #24, story 60): checked at most once
        # per `job_stall_check_interval_polls` idle polls, so a busy
        # worker (always finding work) doesn't pay for it every loop.
        idle_polls_since_stall_check = 0

        try:
            while not stop_event.is_set():
                claimed_something = await claim_and_process_one(
                    pool,
                    object_store,
                    recognizer,
                    source_cache=source_cache,
                    lease_seconds=settings.page_job_lease_seconds,
                    dpi=settings.page_dpi,
                    max_attempts=settings.page_job_max_attempts,
                    figure_captioner=figure_captioner,
                    page_processing_timeout_seconds=settings.page_processing_timeout_seconds,
                )
                if not claimed_something:
                    QUEUE_DEPTH.set(await queries.count_queued_page_jobs(pool))
                    idle_polls_since_stall_check += 1
                    if idle_polls_since_stall_check >= settings.job_stall_check_interval_polls:
                        idle_polls_since_stall_check = 0
                        stalled = await queries.fail_stalled_jobs(pool, settings.job_stall_timeout_seconds)
                        if stalled:
                            logger.warning("marked %d stalled job(s) as failed", stalled)
                    try:
                        await asyncio.wait_for(
                            stop_event.wait(), timeout=settings.worker_poll_interval_seconds
                        )
                    except asyncio.TimeoutError:
                        pass
        finally:
            logger.info("worker shutting down cleanly (SIGTERM/SIGINT)")
            await pool.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
