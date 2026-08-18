# Spec: docolage — self-hosted document parsing on Databricks GPU

## Problem Statement

Teams on Databricks who need to turn unstructured documents (PDF, PNG, DOCX, PPT, Markdown) into queryable structured data currently reach for `ai_parse_document`. It works well, but every call routes through a **Model Provider** — a Databricks-hosted, Databricks-billed inference surface. For users who cannot or will not depend on that path, there is no drop-in alternative.

The alternatives available today are all bad in different ways. Classical OCR libraries produce flat text and lose layout, tables, and reading order entirely. Assembling a custom pipeline from a layout model plus an OCR model plus a table parser means writing and maintaining reading-order logic, heading-hierarchy refinement, and cross-page table merging by hand. And any custom pipeline produces its own bespoke output shape, so downstream SQL written against `ai_parse_document` has to be rewritten to consume it.

What is missing is a way to get `ai_parse_document`-quality structured output — layout understanding, OCR, table understanding, figure understanding, document structure — from models running on the user's own GPU compute, emitting the *same* schema, with no Model Provider anywhere in the path.

## Solution

`docolage` is a Python package (distributed as `ai-docolage`, imported as `docolage`) that parses documents into an `ai_parse_document`-shaped VARIANT/JSON record using PaddleOCR-VL-1.6 loaded from a UC Volume and run in-process on Databricks GPU compute.

The user experience is four steps, and nothing more:

1. **Install the package** onto a GPU cluster.
2. **Upload the model weights** to a UC Volume (downloaded manually, once).
3. **Upload the OpenAI API key** to a Databricks secret scope (optional).
4. **Run it in a GPU notebook** — `docolage.parse(path)` for one document, or `docolage.parse_udf()` inside `mapInPandas` for a whole Volume.

There is no endpoint to provision, no serving infrastructure, no init script, no second cluster, and no Model Provider. Output lands in a Delta table as a VARIANT column whose schema matches `ai_parse_document` field-for-field, so existing downstream queries work unchanged.

Quality comes from mixing precision and speed rather than choosing one. Born-digital pages skip the GPU entirely via the **Router**. Tables, formulas, and charts are handled natively by the **Self-hosted VLM**. Only **Figures** whose VLM output is empty or low-confidence **escalate** to the **External OpenAI API** — so the external dependency stays small, optional, and per-element.

## User Stories

### Installing and setting up

1. As a Databricks user, I want to `pip install ai-docolage` onto a GPU cluster, so that I can parse documents without building anything myself.
2. As a Databricks user, I want the package to declare its own dependencies (PaddleOCR, PaddlePaddle-GPU, pypdfium2, openai), so that a single install command is enough to get running.
3. As a Databricks user, I want to download PaddleOCR-VL-1.6 weights to my laptop and upload them to a UC Volume via the CLI or UI, so that I can provision the model even in a workspace with no outbound internet egress.
4. As a Databricks user, I want to point `docolage` at the UC Volume path holding the weights via configuration, so that I never hardcode a model location in my notebook.
5. As a Databricks user, I want `docolage` to fail immediately with a clear message if the configured model path does not exist or is missing expected weight files, so that I find out at setup time rather than midway through a GPU run.
6. As a Databricks user, I want to store my OpenAI API key in a Databricks secret scope, so that the key never appears in notebook source or output.
7. As a Databricks user, I want `docolage` to fall back to an `OPENAI_API_KEY` environment variable when `dbutils` is unavailable, so that I can develop and test the package off-platform.
8. As a Databricks user, I want `docolage` to run successfully with no OpenAI key configured at all, so that the external dependency is genuinely optional and a key-less run still produces complete output for every element type except low-confidence figure descriptions.

### Parsing a single document

9. As a data engineer, I want to call `docolage.parse(path)` on one document in a notebook, so that I can inspect the result and debug a problematic file without standing up a Spark DataFrame.
10. As a data engineer, I want `parse()` to return a plain Python dict matching the `ai_parse_document` schema, so that I can assert on it, print it, or hand it straight to `json.dumps`.
11. As a data engineer, I want to parse a born-digital PDF, so that I get its text content without paying for GPU inference.
12. As a data engineer, I want to parse a scanned PDF, so that I get OCR'd text from pages that have no embedded text layer.
13. As a data engineer, I want to parse a standalone PNG image, so that single-page scans and screenshots are first-class inputs.
14. As a data engineer, I want to parse a DOCX file, so that Word documents flow through the same pipeline and produce the same schema as PDFs.
15. As a data engineer, I want to parse a PPT/PPTX file, so that slide decks are supported alongside documents.
16. As a data engineer, I want to parse a Markdown file, so that already-structured text is converted to the common schema without wasteful rasterization and OCR.
17. As a data engineer, I want a clear, actionable error when I pass an unsupported file type, so that I immediately know the format is out of scope rather than seeing an obscure failure from a downstream library.

### Layout, structure, and content understanding

18. As a data engineer, I want each detected layout element typed (text, table, figure, formula, header, footer), so that I can filter and process element kinds independently in SQL.
19. As a data engineer, I want every element to carry a bbox with its own `page_id`, so that I can locate any piece of content back on its source page.
20. As a data engineer, I want elements returned in correct reading order, so that concatenating their content reconstructs the document as a human would read it.
21. As a data engineer, I want heading hierarchy preserved, so that I can reconstruct document structure and section nesting.
22. As a data engineer, I want tables rendered as HTML in the element's content, so that table structure survives and matches what `ai_parse_document` produces.
23. As a data engineer, I want a table that spans a page break merged into a single table element, so that I do not have to stitch split tables myself downstream.
24. As a data engineer, I want formulas recognized and emitted as LaTeX, so that mathematical content is preserved rather than mangled into approximate text.
25. As a data engineer, I want charts recognized natively by the VLM without an external API call, so that chart-heavy documents do not inflate my OpenAI spend.
26. As a data engineer, I want each element to carry a `confidence` score, so that I can filter or review low-quality extractions.

### Images and crops

27. As a data engineer, I want every page rasterized to a PNG on a UC Volume with its URI recorded on the page record, so that I can visually inspect any page the parser saw.
28. As a data engineer, I want Table and Figure elements to get their own cropped image files with their own `image_uri`, so that I can review a specific table or figure without opening the whole page.
29. As a data engineer, I want text and formula elements to reference a bbox into the page PNG rather than getting their own crop files, so that storage is not flooded with thousands of tiny per-paragraph images.
30. As a data engineer, I want pages rasterized at 200 DPI, so that small table text and footnotes stay legible to the VLM.

### Figure descriptions and escalation

31. As a data engineer, I want the VLM's own figure description used whenever it is confident, so that most figures cost me nothing extra.
32. As a data engineer, I want a figure whose VLM description is empty or below a confidence threshold escalated to the OpenAI API, so that photos and diagrams the VLM cannot describe still get a usable description.
33. As a data engineer, I want the confidence threshold to be configurable, so that I can tune the precision/cost balance for my own document mix.
34. As a data engineer, I want escalation decided per element within a single run rather than as a job-wide mode, so that one job handles a mixed corpus efficiently.
35. As a data engineer, I want to see which descriptions came from escalation, so that I can audit and cost-attribute external API usage.
36. As a data engineer, I want a failed OpenAI call to leave the VLM's original description in place rather than failing the document, so that a transient API problem never costs me a GPU run.

### Batch processing on Spark

37. As a data engineer, I want `docolage.parse_udf()` to return a `mapInPandas`-ready function, so that I can parse a whole UC Volume of documents in one Spark job.
38. As a data engineer, I want to read documents with `spark.read.format("binaryFile")` and pass that DataFrame straight through, so that I use the standard Databricks ingestion path.
39. As a data engineer, I want the model loaded once per executor rather than once per document, so that model load time is amortized across the batch.
40. As a data engineer, I want the parsed result written to Delta as a VARIANT column, so that I can query nested fields directly in SQL.
41. As a data engineer, I want `parse()` and `parse_udf()` to produce identical output for the same document, so that what I debug on one file is exactly what runs at scale.

### Errors and reliability

42. As a data engineer, I want a single failed page recorded in `error_status` while every other page still parses, so that one bad page never discards the GPU work spent on a long document.
43. As a data engineer, I want `error_status` to be an array of `{error_message, page_id}`, so that it matches `ai_parse_document` and I can query failures in SQL.
44. As a data engineer, I want to find every document with any page failure via a simple SQL predicate, so that I can identify and reprocess gaps.
45. As a data engineer, I want an error message that names the actual cause (render failure, GPU OOM, timeout), so that I can tell transient problems apart from genuinely malformed documents.

### Compatibility

46. As a data engineer already using `ai_parse_document`, I want `docolage`'s output schema to match it field-for-field, so that my existing downstream SQL and parsing code works unchanged.
47. As a data engineer, I want to A/B the two parsers on the same corpus, so that I can measure quality differences before committing to a migration.

## Implementation Decisions

### Package shape and public interface

The package is distributed as `ai-docolage` and imported as `docolage`. It exposes exactly two entry points over one shared implementation (ADR-0004):

- `docolage.parse(path, config) -> dict` — parses a single document, returns the `ai_parse_document`-shaped dict.
- `docolage.parse_udf(config) -> callable` — returns a function suitable for `df.mapInPandas(...)`, loading the model once per executor.

`parse_udf` is a thin wrapper that batches over `parse`'s underlying implementation, so the two cannot drift apart in behavior.

A `config` object carries the UC Volume model path, the output Volume path for page PNGs and crops, the escalation confidence threshold, and OpenAI credential resolution. Configuration is explicit and passed in — no module-level global state, no hardcoded paths.

### Ingestion and format normalization

Formats normalize to one of two paths before any parsing happens:

- **PDF** — used directly.
- **DOCX, PPT/PPTX** — converted to PDF via headless LibreOffice (`soffice --headless --convert-to pdf`), then treated as PDF.
- **PNG** — treated as a single already-rasterized page.
- **Markdown** — bypasses rasterization, the Router, and the GPU entirely. Its existing structure (headings, tables, image links) maps directly onto the element schema. This is both cheaper and strictly more accurate than rendering-then-re-OCRing text that is already text.

Rasterization uses pypdfium2 at a fixed **200 DPI**, writing page PNGs to a UC Volume. Each page's `image_uri` is recorded on its page record.

### The Router

A CPU-only decision made before any GPU work, keyed on **born-digital status alone**. If a PDF page has a usable embedded text layer, its text is extracted directly with no GPU involvement. Everything else goes to the VLM path.

The Router deliberately does **not** attempt to detect tables or figures on born-digital pages. Detecting them cheaply enough to matter would require running layout inference, which defeats the entire purpose of having a CPU-only fast path. The accepted consequence: a born-digital PDF containing a table yields extracted text for that page rather than table HTML.

### VLM execution

`docolage` drives PaddleOCR's own `PaddleOCRVL` pipeline object, **not** raw model prompting (ADR-0005). This is load-bearing: PaddleOCR-VL decomposes into a layout detection model (PP-DocLayoutV3) that always runs in PaddlePaddle and a recognition stage that can be delegated to a backend. Prompting the model directly skips layout analysis, cross-page table merging, and heading-hierarchy refinement — the official docs state plainly it "is not equivalent to executing the full PaddleOCR-VL pipeline."

The VLM recognition stage uses the **transformers backend** for now. vLLM was investigated and deferred: it conflicts with PaddlePaddle at the dependency level and cannot share a virtual environment with it, so adopting it requires a separate venv bootstrapped by a cluster init script plus subprocess and port lifecycle management. At the target scale the throughput difference is minutes, not hours. The upgrade path is deliberately two parameters (`vl_rec_backend="vllm-server"`, `vl_rec_server_url=...`) with no other pipeline change, so starting simple creates no architectural lock-in.

Weights are read from a UC Volume path supplied via config. Nothing is downloaded at runtime.

### Figure escalation

Every Figure is attempted with the VLM first. Escalation to the External OpenAI API happens per element, when the VLM's `description` is empty or its `confidence` falls below the configured threshold (ADR-0003). Tables, formulas, and charts never escalate — the VLM handles them natively.

Escalation is optional end to end. With no key configured, figures simply keep the VLM's output and the run completes normally. A failed OpenAI call degrades the same way rather than failing the document.

The OpenAI key resolves from a Databricks secret scope, falling back to the `OPENAI_API_KEY` environment variable, falling back to no escalation.

### Output schema

The output mirrors `ai_parse_document` field-for-field (ADR-0002). Confirmed structure:

```json
{
  "document": {
    "pages":    [ { "id": "INT", "image_uri": "STRING" } ],
    "elements": [ {
      "id": "INT", "type": "STRING", "content": "STRING",
      "confidence": "DOUBLE", "description": "STRING",
      "bbox": [ { "coord": ["INT"], "page_id": "INT" } ]
    } ]
  },
  "error_status": [ { "error_message": "STRING", "page_id": "INT" } ],
  "metadata": { "id": "STRING", "version": "STRING" }
}
```

Two details that are easy to get wrong and are not negotiable: `elements` sits at the **document** level, not nested inside `pages`, and each element's `bbox` carries its own `page_id`. Figure descriptions belong in `description`; table HTML belongs in `content`.

Persisted to Delta via `to_json()` → `parse_json()` → VARIANT column.

### Error handling

Per-page isolation, mirroring `ai_parse_document` exactly (ADR-0006). A failed page appends a `{error_message, page_id}` entry to the `error_status` array; all other pages parse normally and the document row is still written. There is no checkpoint or resume concept — `ai_parse_document` has none, and neither does `docolage`. Downstream consumers must check `error_status` rather than assume every page produced elements.

### Element crops

Table and Figure elements get standalone cropped image files on the UC Volume with their own `image_uri`. Figures need the crop anyway to escalate; tables benefit from it for human/QA review. Text and formula elements are fully represented by their `content` plus a bbox, so no crop file is written for them.

### Explicitly not built

No Databricks Model Serving endpoint. No `ai_query`. No Foundation Model API. No vLLM HTTP server, local or otherwise. No MLflow Model Registry dependency at runtime. No NLP named-entity extraction — "entity" in this project always means a layout element.

## Testing Decisions

### What makes a good test here

Tests assert on **external behavior only**: the shape and content of the dict returned from the public entry point. A good test says "a scanned PDF produces text elements with non-empty content and correct page_ids" or "a page that fails to render appears in `error_status` while its siblings still parse." A bad test reaches into the router, the cropper, or the schema assembler and asserts on their internals — those are free to be restructured entirely as long as observable output holds.

This matters more than usual because nearly every component here is a candidate for replacement: the VLM backend will likely move to vLLM, the router heuristic may gain sophistication, and the captioner model may change. Tests coupled to internals would have to be rewritten each time; tests written against `parse()`'s output survive all of it.

### The seam

There is **one public seam** — `docolage.parse(path, config) -> dict` — with **two injectable ports** for the dependencies that cannot run in ordinary tests:

- **VLM recognizer** — real implementation wraps `PaddleOCRVL`; fake returns canned layout boxes, types, content, and confidences.
- **Figure captioner** — real implementation wraps the OpenAI client; fake returns canned descriptions and records the calls it received.

Both are supplied through `config`. Everything else — format normalization, rasterization, the Router, cropping, escalation logic, schema assembly, error collection — is exercised *through* this one seam rather than being independently addressable. This is deliberate: fewer seams means fewer things frozen against refactoring.

`parse_udf` gets thin coverage confirming it produces output identical to `parse` for the same input, since it is a wrapper by construction.

### What gets tested

Fixture documents, one per input path, kept small and committed to the repo:

- A born-digital PDF (asserts the Router takes the CPU path and the VLM recognizer is never called)
- A scanned PDF (asserts the VLM path, OCR'd content present)
- A multi-page PDF with a table spanning a page break (asserts cross-page merge into one element, content is HTML)
- A PDF containing a formula (asserts LaTeX in content)
- A standalone PNG
- A DOCX and a PPTX (assert both normalize to the same schema as PDF)
- A Markdown file (asserts the GPU path is bypassed entirely and structure maps to elements)

Behavioral cases driven through the fakes:

- Escalation fires for a figure with confidence below threshold; does not fire for one above it
- Escalation never fires for table, formula, or chart elements regardless of confidence
- With no OpenAI key configured, parsing completes and figures retain VLM descriptions
- A captioner that raises leaves the original description intact and does not fail the document
- A page that fails to render appears in `error_status` with the correct `page_id` while other pages still produce elements
- Schema conformance: `elements` at document level, every bbox carries `page_id`, `error_status` is an array

Two checks worth having beyond unit tests: a schema-conformance assertion that could be run against real `ai_parse_document` output to catch drift, and one GPU smoke test — marked and excluded from the default run — that parses a single real document with the real VLM to confirm the weights load from the Volume and the pipeline actually executes.

### Prior art

None — this is a greenfield repo with no existing tests. The conventions established here become the prior art.

## Out of Scope

- **Chunking and RAG preparation.** The pipeline's job ends at the VARIANT/Delta record. Chunking is a separate layer built on top later.
- **NLP named-entity recognition.** No extraction of people, organizations, dates, or amounts from parsed text.
- **Model Serving deployment.** No serving endpoint, no `ai_query`, no Foundation Model API — this is the project's founding constraint.
- **vLLM backend.** Investigated and deliberately deferred (ADR-0005), with a documented two-parameter upgrade path.
- **MLflow Model Registry.** Earlier discussion favored registering the pyfunc in UC, but the confirmed usage model — install package, upload weights, run in a notebook — does not require it. It can be added later without changing the parse interface.
- **Multi-node and autoscaling GPU clusters.** Target scale is hundreds to low thousands of pages, batch/ad-hoc, on a single-node GPU cluster.
- **Table/figure detection on born-digital pages.** Deliberately excluded from the Router; see the Router decision above.
- **Incremental reprocessing and checkpointing.** `ai_parse_document` has no such concept and neither does this.
- **Formats beyond PDF, PNG, DOCX, PPT/PPTX, and Markdown.**

## Further Notes

**The ADR trail matters.** Six ADRs in `docs/adr/` record decisions where a future reader will reasonably wonder "why on earth was it done this way." ADR-0001 is superseded by ADR-0005 and kept deliberately — it documents an assumption (that vLLM could run PaddleOCR-VL end to end) that is intuitive, wrong, and worth preventing someone from re-adopting.

**The riskiest assumption** is ADR-0003's dependence on a usable per-element confidence signal. The `ai_parse_document` schema exposes `confidence DOUBLE`, which is strong evidence PaddleOCR-VL surfaces something equivalent — but this needs verifying against real output early in implementation. If the signal is unusable, the fallback is a proxy heuristic on description length or emptiness, which is a contained change to the escalation predicate alone.

**Performance intent** is "mix precision and speed," implemented as three tiers rather than a global quality knob: born-digital pages skip the GPU entirely; the VLM handles everything it is good at natively; only genuinely hard figures reach the external API. Each tier is roughly an order of magnitude cheaper than the next, and documents flow to the cheapest tier that can handle them.

**LibreOffice is an undeclared system dependency** for DOCX and PPT. It cannot be pip-installed and must be present on the cluster. This should be checked at parse time with a clear error rather than surfacing as a confusing subprocess failure, and it is the one part of setup that may need a cluster init script despite the "no init script" design goal.
