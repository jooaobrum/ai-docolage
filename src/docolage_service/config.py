"""Service configuration.

Two sources, deliberately not merged into one:

- **docolage.yaml** (docolage.settings_loader), the one source of truth
  for every tunable parameter — models, thresholds, prompts, timeouts,
  DPI, queue/sync knobs. Every default below is read from it at class
  body time; nothing here hardcodes a tunable.
- **Environment variables** (DOCOLAGE_* prefix, per spec user story 8),
  for secrets and per-deployment connection info — the Postgres DSN,
  object storage credentials/endpoint, OPENAI_API_KEY. These vary per
  environment and must never be committed to a file that ships with the
  package, so they keep their own local-dev-friendly literal defaults
  here and are overridden by env vars in every other environment.

Any setting can still be overridden individually via its own DOCOLAGE_*
env var (pydantic-settings' normal behaviour) regardless of which of the
two sources supplied its default — env vars always win at instantiation.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from docolage.openai_client import DEFAULT_MODEL as _DEFAULT_OPENAI_MODEL
from docolage.settings_loader import section

_recognition = section("recognition")
_openai = section("openai")
_office_convert = section("office_convert")
_queue = section("queue")
_submit = section("submit")
_sync_stream = section("sync_stream")
_result = section("result")
_postgres = section("postgres")
_object_storage = section("object_storage")


class Settings(BaseSettings):
    """Runtime configuration for both the API and the Worker processes."""

    model_config = SettingsConfigDict(
        env_prefix="DOCOLAGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Postgres: connection info is a secret/per-deployment env var;
    # pool sizing is a tunable, sourced from docolage.yaml. -------------
    postgres_dsn: str = "postgresql://docolage:docolage@localhost:5432/docolage"
    postgres_pool_min_size: int = _postgres["pool_min_size"]
    postgres_pool_max_size: int = _postgres["pool_max_size"]

    # --- Object storage (S3-compatible; MinIO locally): credentials and
    # endpoint are secrets/per-deployment env vars; region/TLS are
    # tunables, sourced from docolage.yaml. ------------------------------
    object_storage_endpoint_url: str = "http://localhost:9000"
    object_storage_bucket: str = "docolage"
    object_storage_access_key: str = "docolage"
    object_storage_secret_key: str = "docolage123"
    object_storage_region: str = _object_storage["region"]
    object_storage_use_ssl: bool = _object_storage["use_ssl"]

    # --- Queue / worker knobs -------------------------------------------
    page_job_lease_seconds: int = _queue["page_job_lease_seconds"]
    page_job_max_attempts: int = _queue["page_job_max_attempts"]
    worker_poll_interval_seconds: float = _queue["worker_poll_interval_seconds"]

    # Rasterization DPI for the page a Worker renders before recognition.
    # Matches DocolageConfig.dpi's default (docolage/config.py, same
    # docolage.yaml "recognition" section) so the Service and a bare Core
    # call produce the same output at default settings; kept as a
    # separate setting because the Worker never constructs a
    # DocolageConfig (it rasterizes and recognizes directly).
    page_dpi: int = _recognition["dpi"]

    # Timeout for the headless LibreOffice subprocess that normalizes
    # DOCX/PPTX to PDF, both at submit time (page counting) and in the
    # Worker (source-document conversion). Matches
    # DocolageConfig.office_convert_timeout_seconds' default.
    office_convert_timeout_seconds: int = _office_convert["timeout_seconds"]

    # Page-level recognition fallback (docolage.recognizer_fallback):
    # below this docling page confidence (mean_score), the Worker
    # re-recognizes the page via OpenAI instead, if OPENAI_API_KEY is
    # configured. Matches DocolageConfig.recognition_confidence_threshold's
    # default. Has no effect when OPENAI_API_KEY is unset — the fallback
    # never runs in that case regardless of this value.
    recognition_confidence_threshold: float = _recognition["confidence_threshold"]

    # The OpenAI model used by both External OpenAI API ports (figure
    # captioning and the recognition fallback) — one source of truth for
    # a running Worker's model choice, rather than each port defaulting
    # independently (docolage.openai_client.DEFAULT_MODEL is the
    # module-level fallback for direct Core use with no Service running;
    # both read the same docolage.yaml "openai.model" value).
    openai_model: str = _DEFAULT_OPENAI_MODEL

    # --- Per-Worker concurrency (ticket #25, spec "Unresolved, pending
    # measurement": "the per-Worker concurrency (pages in flight per
    # Worker, bounded by model VRAM)") -----------------------------------
    # The worker loop (ticket #21, src/docolage_service/worker/main.py's
    # `run()`) claims and processes exactly one page_job at a time, fully
    # sequentially, per process — there is no in-process concurrency here.
    # That is a deliberate reading of the spec, not an oversight: user
    # story 39 says a Worker "claim[s] exactly one page job at a time",
    # and ADR-0010 says "Worker count is therefore also GPU count" — i.e.
    # the scaling unit is the *process* (one docling model set per
    # process, `docker compose up --scale worker=N`), not a thread/async
    # pool inside one process racing for the same model's VRAM. Running N
    # pages concurrently through a single loaded model would only pay off
    # if docling's pipeline released the GIL and shared VRAM safely across
    # concurrent calls, which it is not built to promise.
    #
    # This setting is therefore *not* wired to any in-process concurrency
    # in the worker loop. It exists to make explicit, and configurable,
    # the guidance that would otherwise live only in an operator's head:
    # "how many Worker processes/replicas should I run against one GPU?"
    # A value of 1 means "one Worker process fully occupies one GPU's
    # VRAM for one docling model set" (the common case, and this
    # setting's default). Set it above 1 only if you have measured that
    # your GPU has enough spare VRAM to safely hold multiple docling model
    # sets, and are willing to run `--scale worker=N` with N processes
    # sharing that GPU (e.g. via `deploy.resources.reservations.devices`
    # in docker-compose.gpu.yml) rather than the worker loop itself doing
    # in-process concurrency. Logged at Worker startup for operator
    # visibility.
    worker_page_concurrency: int = _queue["worker_page_concurrency"]

    # Wedged-job timeout (ticket #24, spec user story 60): a 'running' job
    # whose page_jobs have made no progress (no claim/complete/fail) for
    # this long is marked 'failed'. Checked periodically in the Worker's
    # claim loop, at most once per `job_stall_check_interval_polls` idle
    # polls, so it costs one cheap UPDATE ... WHERE per idle period rather
    # than per loop iteration.
    job_stall_timeout_seconds: float = _queue["job_stall_timeout_seconds"]
    job_stall_check_interval_polls: int = _queue["job_stall_check_interval_polls"]

    # Bounds a single page's rasterize+recognize+caption call (see
    # worker/main.py's `_process`). A hung docling/OpenAI call on one page
    # must not block the worker forever; see docolage.yaml's comment on
    # this key for the cancellation caveat (the OS thread keeps running
    # until it finishes naturally -- this bounds queue-visible staleness,
    # not the compute itself).
    page_processing_timeout_seconds: float = _queue["page_processing_timeout_seconds"]

    # Port the Worker exposes its own GET /metrics on (prometheus_client's
    # start_http_server) -- a separate port from the API's since the
    # Worker is not an HTTP service otherwise.
    worker_metrics_port: int = _queue["worker_metrics_port"]

    # --- Submit limits ----------------------------------------------------
    # sync_gate_max_pages was measured: benchmark/measure_worker_defaults.py
    # on 2026-08-17, CPU only (no GPU available in the measuring
    # environment). 14 pages sampled across all 4 files in data/
    # (benchmark/manifest.json) via DoclingRecognizer.recognize() gave a
    # mean per-page latency of ~27.8s/page (p50 29.85s, p95/max 56.56s) on
    # CPU. Backed into a page count that keeps a sync request within a
    # ~45s wall-clock budget, that measurement floors to 1 page/
    # sync-request on this hardware — a real, not-guessed number, but a
    # CPU number, and a conservative one: TableFormer + OCR fallback
    # dominate this cost and both are expected to drop substantially on a
    # GPU. Kept as-is (not artificially inflated) in docolage.yaml so the
    # default is honest about what was actually measured; raise it there
    # after re-running this benchmark on the target GPU host. See
    # docs/operations.md and benchmark/worker_defaults.json for the full
    # methodology and raw numbers.
    max_upload_bytes: int = _submit["max_upload_bytes"]
    sync_gate_max_pages: int = _submit["sync_gate_max_pages"]

    # --- Result retention --------------------------------------------------
    result_retention_days: int = _result["retention_days"]

    # --- Sync stream (ticket #23, ADR-0008: poll page_results on a short
    # interval, tracking a high-water mark per stream) -------------------
    # ADR-0008 specifies "~300-500ms"; docolage.yaml's 0.4s splits that
    # range.
    sse_poll_interval_seconds: float = _sync_stream["poll_interval_seconds"]
    # Measured alongside sync_gate_max_pages above (same benchmark run):
    # sync_gate_max_pages (1) x mean per-page latency (~27.8s) x a 2x
    # margin (covering queue wait plus slower-than-mean pages; p95 was
    # 56.56s, i.e. a single slow page alone can approach this budget) =
    # ~55.7s, rounded up to docolage.yaml's 60.0s. Re-run on the target
    # GPU host and update that value before relying on it in production.
    sync_stream_timeout_seconds: float = _sync_stream["timeout_seconds"]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings singleton.

    Cached so repeated calls (e.g. across FastAPI dependencies) don't
    re-parse the environment; tests that need different settings should
    construct `Settings(...)` directly rather than mutate this cache.
    """
    return Settings()
