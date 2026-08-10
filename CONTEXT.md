# Databricks Doc Parser

A self-hosted document intelligence pipeline on Databricks GPU compute that mirrors `ai_parse_document`'s output shape (layout, OCR, tables, figures, structure → VARIANT/JSON) without depending on Databricks-hosted model endpoints. Shipped as a Python package named **docolage**.

**Out of scope (deliberately, for now)**: chunking strategies and any downstream RAG-prep enhancements. The pipeline's job ends at the VARIANT/Delta record — chunking is a later, separate concern layered on top, not part of `docolage`'s parse output.

**Supported formats**: PDF, PNG, Markdown, and (via headless LibreOffice conversion to PDF) DOCX/PPTX. DOCX/PPTX requires the `soffice` binary on the host/cluster — see [docs/office-formats.md](docs/office-formats.md) for the cluster requirement and install instructions.

## Language

**Model Provider (forbidden)**:
Any Databricks-hosted inference surface billed/routed through Databricks — `ai_query()`, Databricks Foundation Model APIs, or a Databricks Model Serving pay-per-token endpoint. The pipeline must not depend on these.
_Avoid_: "external model", "hosted model" (too broad — the forbidden thing is specifically the Databricks-billed serving path, not "any API call to any model")

**Self-hosted VLM**:
PaddleOCR-VL-1.6, whose weights are downloaded manually, uploaded to a UC Volume, and loaded directly into the Databricks GPU notebook process. Not a Model Provider — nothing is hosted, billed, or served; the weights are read off the Volume and run in-process. Driven through PaddleOCR's own `PaddleOCRVL` pipeline (layout detection in PaddlePaddle, recognition via the transformers backend), never by prompting the raw model — see ADR-0005.
_Avoid_: "endpoint", "serving" (there is deliberately no HTTP surface, local or otherwise, in the current design)

**VLM recognizer** / **Figure captioner**:
The two injectable ports that isolate `docolage`'s non-hermetic dependencies. The VLM recognizer wraps the Self-hosted VLM; the Figure captioner wraps the External OpenAI API. Each has a real implementation and a fake, so the whole pipeline is testable through one public seam without a GPU or a network call.

**External OpenAI API**:
The real api.openai.com service, called via the `openai` Python client with an API key, for sub-tasks where a self-hosted model is intentionally skipped (e.g. figure/chart captioning). Explicitly allowed by the user, unlike a Databricks Model Provider — the constraint is about avoiding Databricks-billed endpoints, not about avoiding third-party APIs in general.

**Router**:
The CPU-only, pre-GPU decision that sends a page down the text-layer path (born-digital PDF, extract the embedded text layer directly, no GPU) or the VLM path (everything else — scanned pages, images, and any born-digital page, since the router does not attempt to detect tables/figures on a text-layer page). Routing is on born-digital status alone.
_Avoid_: "table/figure pre-check" (deliberately not part of the Router — detecting tables/figures cheaply enough to matter would require running layout inference, defeating the point of a CPU-only path)

**Figure**:
A `type='figure'` element from PaddleOCR-VL's layout output — a crop that is not text, a table, or a formula (e.g. a photo or diagram). Figures are always attempted with PaddleOCR-VL first; only figures whose PaddleOCR-VL output is empty/low-confidence escalate to the External OpenAI API. Charts, tables, and formulas are handled natively by PaddleOCR-VL and never escalate.
_Avoid_: "chart" as a synonym (charts are handled natively by PaddleOCR-VL — conflating them would over-trigger escalation)

**Escalation**:
The confidence/emptiness-based decision to call the External OpenAI API for a specific Figure, made per-element after PaddleOCR-VL has already produced (or failed to produce) a `description` for it, thresholded on the element's `confidence`. Not a per-job or per-document setting — the mix happens within a single job. Escalation is optional: with no OpenAI key configured, figures simply keep PaddleOCR-VL's output.
_Avoid_: "quality mode", "fallback" (this isn't a coarse job-level switch, and it isn't triggered by failure/errors — a confident-but-empty PaddleOCR-VL result also escalates)

**Element crop**:
A standalone cropped image file persisted to the UC Volume (with its own `image_uri`) for a Table or Figure element only. Text and formula elements are represented by their extracted content (text/LaTeX) plus a bbox into the page PNG — no separate crop file.
_Avoid_: "entity image" (this project's "entity" always means a layout element, never an NLP named entity — NLP entity extraction is explicitly out of scope)

**docolage**:
The Python package name for this whole pipeline. Exposes two entry points over the same underlying pyfunc: `docolage.parse(path)` for a single document, and `docolage.parse_udf()` returning a `mapInPandas`-ready function for batch runs over a Spark DataFrame.
