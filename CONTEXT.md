# Docolage

A self-hosted document intelligence pipeline that turns documents into structured layout, text, tables and figures. Shipped as a Python package named **docolage**, served by a small HTTP service with a synchronous and an asynchronous path.

**Out of scope (deliberately, for now)**: chunking strategies and any downstream RAG-prep enhancements. The pipeline's job ends at the parse result — chunking is a later, separate concern layered on top.

**Supported formats**: PDF, PNG, Markdown, and (via headless LibreOffice conversion to PDF) DOCX/PPTX. DOCX/PPTX requires the `soffice` binary on the host — see [docs/office-formats.md](docs/office-formats.md).

**Operations**: running Workers on a real GPU, and how the Sync gate / sync stream timeout defaults were measured — see [docs/operations.md](docs/operations.md).

## Language

**Core**:
`docolage.parse()` and everything beneath it — format normalization, page rasterization, recognition, cropping, captioning, schema assembly. Pure Python, no queue, no HTTP, no knowledge of jobs. The Core is what the Service calls; it is not itself a service.
_Avoid_: "the pipeline" as a synonym for the Core (the pipeline includes the Service and the workers)

**Service**:
The deployable shell around the Core: an HTTP API, a Postgres-backed queue, and a pool of Workers. Everything in the Service is new; nothing in it is Databricks- or Spark-specific.

**Sync path** / **Async path**:
Two delivery contracts over **one** pipeline, one queue and one Worker pool — they are not separate topologies. The Sync path holds the client's connection and streams results per page as they land (SSE). The Async path returns a job id immediately and the client polls for status and the final result. A document that exceeds the sync gate is rejected from the Sync path and must be submitted asynchronously.
_Avoid_: "sync folder" (there is no watched directory anywhere in the design), "sync pipeline"/"async pipeline" (implies two pipelines; there is one)

**Sync gate**:
The size/page-count limit above which a submission is refused on the Sync path. Its purpose is to bound how long a client connection and a Worker slot can be held.

**Page job**:
The unit of work on the queue: exactly one page of one document. Chosen over whole-document jobs so pages parallelise across Workers, fail in isolation, and stream progressively on the Sync path. The cost is deliberate and accepted: no cross-page structure (reading order across pages, table continuation), so a **Reassembly** step must order page results back into one document result.

**Worker**:
A process that claims Page jobs from the queue and runs the Core on them. Workers load the docling models in-process — there is no model-serving tier and no HTTP hop to a GPU. Worker count is therefore also GPU count.
_Avoid_: "vLLM worker", "inference server" (deliberately not part of the MVP; see the Recognizer port)

**Recognizer**:
The injectable port isolating the model-bearing dependency, with a real implementation and a fake so the Core is testable without a GPU. The MVP implementation wraps **docling's standard pipeline** (layout model + TableFormer for tables + OCR only where a page lacks a usable text layer). The port exists precisely so a served-model implementation can replace it later without touching the Core.

**Recognition fallback**:
Page-level escalation (ADR-0012) from docling to the **External OpenAI API**: below a configured confidence threshold on docling's own per-page confidence report, the whole page is re-recognized by an OpenAI-backed Recognizer instead. Distinct from the earlier, now-superseded per-figure escalation (ADR-0003) — this thresholds on docling's page-level confidence, the only granularity docling still exposes, not a per-element signal. Silently absent with no `OPENAI_API_KEY` configured, same as the Figure captioner.
_Avoid_: using "escalation" unqualified for this — the term now has two distinct meanings in the codebase (this page-level one, and the retired per-figure one from ADR-0003); say "recognition fallback" for this one.

**Figure captioner**:
The second injectable port, wrapping the **External OpenAI API**, which supplies descriptions for figures. docling returns figures with no description, so captioning is unconditional rather than confidence-triggered: every figure is captioned if a key is configured, and figures simply carry no description if one is not.
_Avoid_: "escalation" for figure captioning specifically (there is no docling-produced description to escalate from — the earlier confidence-thresholded meaning no longer exists here; see **Recognition fallback** for where confidence-based escalation now lives instead)

**External OpenAI API**:
The real api.openai.com service, called via the `openai` client with an API key, for sub-tasks where a self-hosted model is intentionally skipped (figure captioning).

**Figure**:
A picture element from docling's layout output — a crop that is not text, a table, or a formula (e.g. a photo or diagram). Tables and formulas are handled natively by docling and are never sent to the Figure captioner.
_Avoid_: "chart" as a synonym

**Element crop**:
A standalone cropped image persisted to object storage (with its own URI) for a Table or Figure element only. Text and formula elements are represented by their extracted content (text/LaTeX) plus a bbox into the page image — no separate crop file.
_Avoid_: "entity image" (this project's "entity" always means a layout element, never an NLP named entity)

**Object storage**:
S3-compatible storage (MinIO in development) holding uploads, page images, and Element crops. Together with Postgres — which holds job metadata, the queue, and page results — it is the **only** infrastructure the MVP depends on. There is deliberately no Redis, no Kafka, and no RabbitMQ.
_Avoid_: "UC Volume" (Databricks-specific; retired)
