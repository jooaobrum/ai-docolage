# Deploying behind a firewall that blocks Hugging Face

## The problem

`DoclingRecognizer` (`src/docolage/recognizer_docling.py`) uses docling's
standard pipeline, which lazily downloads its models on first use:

- the layout model and TableFormer, from `huggingface.co`
- RapidOCR's detection/classification/recognition checkpoints, from
  `modelscope.cn` (the default RapidOCR backend, `torch`)

If a Worker's egress can't reach those hosts, the first page it tries to
process fails (or hangs, depending on your firewall's behavior on a
blocked connection) — this is what you'll see if you deploy straight
into a locked-down network without doing the steps below first.

The fix has two parts: **prefetch the models on a machine that *can*
reach the internet**, then **point the Worker at that local copy** —
using `docling-tools`, which every `docling` install already includes,
and a native docling setting, so nothing reaches out at runtime.

## Step 1 — prefetch the models (on a machine with internet access)

This can be your laptop, a bastion host, or a CI runner — anywhere with
egress. It needs `docling` installed (the same version pinned in
`pyproject.toml`, so the cached artifacts match what the Worker image
expects):

```bash
pip install "docling>=2.0.0"   # or: pip install -e ".[service]" from this repo

docling-tools models download --all --output-dir ./docling-models
```

This downloads the layout model, TableFormer, and the default RapidOCR
checkpoint set into `./docling-models` (docling's own on-disk layout —
don't restructure it). To fetch a *subset* instead of `--all` (smaller
image, fewer models to vet through your firewall's allowlist review):

```bash
docling-tools models download layout tableformer rapidocr -o ./docling-models
```

`docling-tools models download --help` lists every available model name
if you want to trim further (e.g. skip `picture_classifier` if you don't
need it) — check what `DoclingRecognizer` actually exercises (layout +
tableformer + the OCR engine it falls back to) before dropping anything.

Verify what landed:

```bash
du -sh ./docling-models
find ./docling-models -maxdepth 2
```

## Step 2 — ship the model directory into the Worker image

Add the prefetched directory to the build context and `COPY` it into
`Dockerfile.service` (and `Dockerfile.worker-gpu` if you're using the
GPU variant), rather than mounting it as a runtime volume — that way the
image itself is fully self-contained and reproducible:

```dockerfile
# Dockerfile.service, after the existing COPY src ./src line:
COPY docling-models /opt/docling-models
```

(Put `docling-models/` at the repo root, next to `Dockerfile.service`,
so it's inside the default build context — or point `docker build`'s
context at wherever you keep it.)

If you'd rather not bloat the image, mount it as a read-only volume at
container-start time instead — functionally equivalent, just decide
once and be consistent between build and deploy:

```yaml
# docker-compose.yml, worker service
volumes:
  - ./docling-models:/opt/docling-models:ro
```

## Step 3 — point docling at the local copy

docling reads `DOCLING_ARTIFACTS_PATH` from the environment natively
(`docling.datamodel.settings.AppSettings`, `env_prefix="DOCLING_"`) —
every pipeline stage (layout, TableFormer, RapidOCR) falls back to it
when no path is set in code, so **no code change is needed** in
`DoclingRecognizer`. Just set the env var on the `worker` service:

```yaml
# docker-compose.yml, worker service
environment:
  DOCLING_ARTIFACTS_PATH: /opt/docling-models
  # ...existing DOCOLAGE_* vars...
```

## Step 4 — make the firewall block a hard failure, not a hang

Belt-and-suspenders: also set Hugging Face's own offline flags, so that
if `DOCLING_ARTIFACTS_PATH` is ever missing or incomplete, the Worker
fails fast with a clear "not found locally" error instead of hanging on
a blocked outbound connection for however long your firewall takes to
time it out:

```yaml
environment:
  HF_HUB_OFFLINE: "1"
  TRANSFORMERS_OFFLINE: "1"
```

## Step 5 — verify before rolling out

Rebuild and bring up just the worker, with no route to `huggingface.co`
or `modelscope.cn` available (pull the network cable, or test in the
actual restricted network segment — testing on an open network proves
nothing here):

```bash
docker compose build worker
docker compose up worker
```

Submit a small document through the API and confirm a page actually
processes (`docker compose logs -f worker` should show `claimed`
directly followed by `succeeded`, with no `httpx`/`huggingface_hub`
network log lines in between — those lines are exactly what you saw in
an open-network run and are the tell that it's still trying to reach
the internet).

## Keeping the model cache current

Models are pinned by whatever `docling` version you prefetched with —
upgrading `docling` in `pyproject.toml` means re-running Step 1 with the
new version before rebuilding the image, or the Worker will look for a
model revision that isn't in your local cache and fail the same way as
an unreachable Hugging Face. Treat `docling-models/` as a versioned build
artifact tied to the `docling` pin, not a one-time download.
