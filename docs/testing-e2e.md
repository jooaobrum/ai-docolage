# End-to-end manual testing guide

This walks through standing up the real stack with `docker compose` and
exercising every path by hand: health, async submit → status → result,
and the sync SSE stream. It complements (doesn't replace) the automated
suites below.

## 0. Automated tests first (fast sanity check)

```bash
# Core + Service unit tests, no infra needed (~1s)
python -m pytest -m "not gpu and not service and not docling"

# Real Postgres/MinIO integration tests via testcontainers — needs Docker
# running. On macOS with Docker Desktop, testcontainers' "Ryuk" reaper
# container sometimes fails to start (socket-mount error); if you see
# that, disable it for the run:
TESTCONTAINERS_RYUK_DISABLED=true python -m pytest -m service tests/service
```

Both should be green before you bother with the manual walkthrough below
— they cover the same logic (claim atomicity, retries, SSE ordering,
reassembly) faster and without needing to read HTTP responses by eye.

## 1. Bring up the stack

```bash
docker compose up --build
```

This starts `postgres`, `minio`, `api` (runs migrations + ensures the
bucket exist on startup), and one `worker`. Wait for logs to settle —
you want to see the API's Uvicorn "Application startup complete" and the
worker's "waiting for work" (or equivalent) log line.

Scale workers if you want to see concurrent claiming in action:

```bash
docker compose up --build --scale worker=3
```

Leave this running in one terminal; run the commands below from another.

## 2. Health check

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

Expect `{"status": "ok", "postgres": true, "object_storage": true}` with
a `200`. Stop `postgres` (`docker compose stop postgres`) and re-run to
see it flip to degraded/`503` — then start it again
(`docker compose start postgres`) before continuing.

## 3. Async path: submit → status → result

Use a small sample PDF from `data/` (repo root):

```bash
JOB_ID=$(curl -s -X POST http://localhost:8000/submit \
  -F "file=@data/TSMCJ.PDF" \
  -F "delivery=async" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "job: $JOB_ID"
```

Poll status until it's terminal (`completed`/`partial`/`failed`):

```bash
watch -n 1 "curl -s http://localhost:8000/jobs/$JOB_ID"
```

(Or without `watch`: `curl -s http://localhost:8000/jobs/$JOB_ID | python3 -m json.tool`, repeated.)
You should see `pages_done` climb from `0` towards `pages_total` as the
worker(s) claim and process pages, then `status` flip to `completed`.

Fetch the result both ways once it's done:

```bash
curl -s "http://localhost:8000/jobs/$JOB_ID/result?format=markdown" | python3 -m json.tool
curl -s "http://localhost:8000/jobs/$JOB_ID/result?format=elements" | python3 -m json.tool
```

`markdown` gives you `{pages: [{page_number, markdown}, ...]}` in page
order; `elements` gives the full `ai_parse_document`-shaped document
(`document.pages[]`, `document.elements[]`, `error_status`).

Try fetching the result of a job that's still running (submit a bigger
document, e.g. `data/stmicro-financial-report.pdf`, and immediately
curl its result) — expect `409`, never a partial document.

## 4. Sync path: SSE stream

**Note**: the production default `sync_gate_max_pages` is deliberately
small (see `docs/operations.md` — it's a CPU-measured value; expect it
to rise substantially on real GPU hardware). Check the current value:

```bash
docker compose exec api env | grep DOCOLAGE_SYNC_GATE_MAX_PAGES
```

If unset, it's using the code default (`1` page at time of writing). To
demo the sync/SSE path with a multi-page document, either use a 1-page
file (a PNG, or a single-page PDF), or raise the gate for your local
stack by adding to `docker-compose.yml`'s `api`/`worker` environment:

```yaml
DOCOLAGE_SYNC_GATE_MAX_PAGES: "50"
```

then `docker compose up -d --build`.

Stream a submission with `curl -N` (no buffering) so you see events as
they arrive:

```bash
curl -N -X POST http://localhost:8000/submit \
  -F "file=@data/TSMCJ.PDF" \
  -F "delivery=sync" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])"
```

Submitting on the sync path just gets you a job id like async does — the
stream is a separate GET. Open it in one terminal:

```bash
curl -N "http://localhost:8000/jobs/$JOB_ID/stream"
```

You should see a sequence of `data: {...}` lines — one `type: "page"`
event per page **in page order** regardless of which worker finishes
which page first, followed by a terminal `type: "complete"` event
carrying the job's final status. Ctrl-C to disconnect mid-stream, then
check `curl http://localhost:8000/jobs/$JOB_ID` afterward — the job
should still be `running`/`completed` on its own; disconnecting the
stream must never cancel it.

Submit something over the sync gate on the `sync` delivery mode and
confirm you get a `413` naming the limit and telling you to use `async`
instead.

## 5. Failure/retry behaviour (optional, more involved)

To see a permanently-failed page surfaced end-to-end, you'd need to
inject a failure into the recognizer, which isn't exposed through the
HTTP API by design (no fault-injection endpoint) — this path is what
`tests/service/test_retry_and_lease.py` and
`tests/service/test_sync_stream.py::test_sync_stream_surfaces_permanently_failed_page`
already exercise against real Postgres via `pytest -m service`. Trust
those over trying to reproduce a failure by hand.

## 6. Tear down

```bash
docker compose down -v   # -v also drops the postgres/minio volumes
```

## Troubleshooting

- **`docker: Cannot connect to the Docker daemon`** — start Docker
  Desktop (or your Docker engine) before `docker compose up` or running
  `pytest -m service`.
- **testcontainers "Ryuk" / socket-mount error on macOS** — set
  `TESTCONTAINERS_RYUK_DISABLED=true` for the pytest run (see §0).
- **`pytest`/`python -m pytest` reports "No tests collected" but you can
  see the test file** — something in your shell profile is intercepting
  the `pytest`/`python` command names. Use `command python -m pytest ...`
  (the `command` builtin bypasses shell functions/aliases) to force the
  real interpreter.
- **Office format (DOCX/PPTX) submissions fail with a LibreOffice
  error** — `soffice` isn't on the container's `PATH`. `Dockerfile.service`
  is expected to install it; if you're running the API/worker outside
  Docker, install LibreOffice locally or skip office-format testing.
