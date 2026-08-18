# Deploying behind a firewall that blocks Hugging Face

## The problem

`DoclingRecognizer` (`src/docolage/recognizer_docling.py`) uses docling's
standard pipeline, which lazily downloads its models on first use:

- the layout model and TableFormer, from `huggingface.co`
- RapidOCR's detection/classification/recognition checkpoints, from
  `modelscope.cn` (the default RapidOCR backend, `torch`) — via a plain
  `requests.get`, **not** `huggingface_hub`, so `HF_HUB_OFFLINE` alone does
  not stop it

If a Worker's egress can't reach those hosts, the first page it tries to
process fails (or hangs, depending on your firewall's behavior on a
blocked connection).

## The fix is already built in

As of this Dockerfile revision, `Dockerfile.service` and
`Dockerfile.worker-gpu` prefetch the models **at build time** and bake
them into the image — nothing to do at deploy time, and no manual
`docling-tools` step required. Building the image already requires
internet access (to reach PyPI for the CPU/GPU torch wheel and docling's
own dependencies), so the model prefetch rides along on that same
build-time egress:

```dockerfile
# builder stage
RUN docling-tools models download layout tableformer rapidocr
```

```dockerfile
# runtime stage
COPY --from=builder /root/.cache/docling /root/.cache/docling
ENV HF_HUB_OFFLINE=1
ENV HF_HUB_DISABLE_TELEMETRY=1
ENV DOCLING_ARTIFACTS_PATH=/root/.cache/docling/models
```

Only `layout`, `tableformer`, and `rapidocr` are prefetched — the exact
set `DoclingRecognizer` exercises (its `PdfPipelineOptions` are all
defaults: `do_table_structure=True`, `do_ocr=True` via the default
RapidOCR engine, and `do_picture_classification` /
`do_code_enrichment` / `do_formula_enrichment` all `False`). Skipping
`picture_classifier`, `code_formula`, and `easyocr` keeps the image
smaller; if you enable any of those options in `PdfPipelineOptions`
later, add the matching model name to the `docling-tools models
download` line in both Dockerfiles.

`DOCLING_ARTIFACTS_PATH` is the load-bearing piece, not
belt-and-suspenders: `HF_HUB_OFFLINE` covers the layout/TableFormer
models (resolved via `huggingface_hub`), but RapidOCR's default engine
resolves its own checkpoint path independently and, left unset, falls
back to `modelscope.cn` regardless of `HF_HUB_OFFLINE`. Setting this env
var to the exact directory `docling-tools models download` populated is
what makes RapidOCR use the baked-in checkpoint instead of reaching out.

## Building the image (needs internet, once)

Build on any machine with normal internet access — your laptop, CI, a
bastion host:

```bash
docker compose build          # or: make build
```

This is the only step in the whole flow that needs to reach
`huggingface.co` / `pypi.org` / the PyTorch wheel index. The resulting
image is self-contained.

## Getting the image onto the firewalled host

If the target host has no registry access either, save/load a tarball:

```bash
make save                     # docker save -> docolage-images.tar
# copy docolage-images.tar onto the offline host by whatever means your
# environment allows (removable media, an internal artifact store, ...)
make load                     # docker load -i docolage-images.tar
```

If the offline host *can* reach an internal/private registry, push there
instead and skip the tarball.

## Running on the firewalled host

No special flags needed — `HF_HUB_OFFLINE` and `DOCLING_ARTIFACTS_PATH`
are already set in the image:

```bash
make up
```

## Verifying before rolling out

Prove it with the network actually cut, not just "no key configured" —
testing on an open network proves nothing here:

```bash
docker run --rm --network none ai-docolage-worker:latest python -c \
  "from docolage.recognizer_docling import DoclingRecognizer; DoclingRecognizer()"
```

This should print RapidOCR's `Using <path>` log lines pointing at
`/root/.cache/docling/models/RapidOcr/...` and exit cleanly — no
`NameResolutionError` / `modelscope.cn` / `huggingface.co` traceback. In
the running stack, submit a small document through the API and confirm
`docker compose logs -f worker` shows `claimed` directly followed by
`succeeded`, with no outbound HTTP log lines to either host in between.

## Keeping the model cache current

Models are pinned by whatever `docling` version was installed at *build*
time — bumping `docling` in `pyproject.toml` means rebuilding the image
(the `docling-tools models download` step re-runs and fetches whatever
that new version expects). There's no separate cache to manage by hand;
it lives and moves with the image build.

## Escalation (OpenAI figure captioning) is a separate, orthogonal concern

If you use `DOCOLAGE_FIGURE_CAPTIONER=openai` (ADR-0010 "Escalation"),
that path calls the External OpenAI API over HTTPS and is unrelated to
the docling models covered here — a fully firewalled deployment should
leave `OPENAI_API_KEY` unset, which disables captioning outright rather
than failing at request time.
