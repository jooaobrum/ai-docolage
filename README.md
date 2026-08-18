# ai-docolage

![ai-docolage logo](docs/imgs/logo.png)

A self-hosted document intelligence pipeline that turns documents (PDF,
PNG, Markdown, DOCX, PPTX) into structured layout, text, tables and
figures — no cloud account, no cluster, no vendor lock-in.

Shipped as a Python package, **docolage** (the Core), and a small
deployable service, **docolage_service**, that wraps it with an HTTP API,
a Postgres-backed job queue, and a pool of Workers. See
[CONTEXT.md](CONTEXT.md) for the full domain vocabulary (Core vs.
Service, Sync/Async paths, Recognizer, Figure captioner, etc.) — read
that first if you're new to the project, it defines the terms the rest of
the docs assume.

## Project layout

```
src/docolage/            Core: parse(), format normalization, rasterization,
                          recognition, cropping, captioning, schema assembly.
                          Pure Python — no queue, no HTTP.
src/docolage_service/    Deployable shell: HTTP API, Postgres queue, Workers.
benchmark/                Latency/accuracy measurement harness + ground truth.
docs/adr/                 Architecture decision records.
docs/spec/                 Specs the Core and Service were built against.
tests/                     Unit + service tests (pytest).
docker-compose.yml         api + worker + postgres + minio, CPU-only.
docker-compose.gpu.yml     GPU override for the worker service.
```

Further reading: [docs/architecture.md](docs/architecture.md),
[docs/operations.md](docs/operations.md) (GPU workers, measured
Sync-gate defaults), [docs/office-formats.md](docs/office-formats.md)
(DOCX/PPTX via LibreOffice), [docs/deploy-airgapped.md](docs/deploy-airgapped.md),
[docs/testing-e2e.md](docs/testing-e2e.md).

## Prerequisites

- Python >= 3.10
- [Docker](https://docs.docker.com/get-docker/) + Docker Compose (to run
  the Service locally)
- LibreOffice (`soffice` on `PATH`), only if you need DOCX/PPTX support —
  see [docs/office-formats.md](docs/office-formats.md) for install
  instructions per OS
- An `OPENAI_API_KEY`, only if you want Figure captioning or the
  page-level recognition fallback (ADR-0012). Both are optional: without
  a key, figures simply carry no description and no fallback runs —
  nothing fails.

## Setup

Clone the repo, then create a virtualenv and install the package in
editable mode with the dev extras:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Optional extras, installed the same way (stack them as needed, e.g.
`pip install -e ".[dev,openai,service]"`):

- `openai` — real Figure captioner / recognition fallback backed by the
  External OpenAI API
- `service` — FastAPI, asyncpg, boto3 and everything `docolage_service`
  needs to run the HTTP API and Workers outside Docker

### Environment variables

Copy `.env` (create it if it doesn't exist) with your own key — it's
gitignored and never committed:

```bash
echo "OPENAI_API_KEY=sk-..." > .env
```

`OPENAI_API_KEY` is the only secret the project needs locally. Everything
else tunable (models, thresholds, prompts, DPI, queue/sync knobs) lives
in `src/docolage/docolage.yaml`, the single source of truth read by both
`DocolageConfig` and `Settings` — see the comment at the top of that
file. Service-only connection info (Postgres DSN, object storage
endpoint/credentials) is supplied via `DOCOLAGE_*` environment variables,
already wired up with local defaults in `docker-compose.yml`.

## Running tests

```bash
pytest
```

The default run excludes GPU, Service-integration, and real-docling-model
smoke tests (see the `markers` in `pyproject.toml`). To run those too:

```bash
pytest -m gpu           # requires a real GPU
pytest -m service       # requires real Postgres/MinIO (testcontainers)
pytest -m docling        # loads real docling models, slower
```

## Running the Service locally

The whole stack — API, Worker, Postgres, MinIO — comes up with one
command, no cloud account or cluster required:

```bash
docker compose up --scale worker=3
```

- API: `http://localhost:8000`
- MinIO console: `http://localhost:9001` (user/pass: `docolage` /
  `docolage123`, dev-only defaults — see `docker-compose.yml`)
- `OPENAI_API_KEY`, if set on your host shell before `docker compose up`,
  is forwarded to the `worker` service automatically.

To run Workers against a real GPU instead of CPU, layer the GPU override
on top:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --scale worker=2
```

See [docs/operations.md](docs/operations.md) for details, including how
the `sync_gate_max_pages` / `sync_stream_timeout_seconds` defaults were
measured and how to re-measure them on real hardware.

## Using the Core directly (no Service)

```python
import docolage

result = docolage.parse("path/to/document.pdf")
```

No queue, no HTTP, no Postgres required — just the Python package and,
optionally, `OPENAI_API_KEY` in the environment for Figure captions and
recognition fallback.
