# Spec: docolage Service — document parsing off Databricks

## Problem Statement

`docolage` today only runs inside Databricks. Parsing a document means having a Databricks workspace, a GPU cluster, a UC Volume with model weights on it, and a notebook to run — and batch throughput means a Spark job. Anyone who wants to parse a PDF has to be a Databricks user first.

That is the wrong shape for three reasons. A GPU cluster is expensive to keep warm and slow to start, so ad-hoc parsing of a handful of documents carries minutes of cluster spin-up before any work happens. There is no way for an application to *call* the parser — the only interfaces are a Python function in a notebook and a Spark UDF, so any product built on top has to be a Spark job too. And a user waiting on a single small document has no way to get results progressively; they wait for the whole run.

Separately, the model stack has been a source of friction. PaddleOCR-VL requires PaddlePaddle, which conflicts with vLLM, and its pipeline must be driven through a specific object with weights laid out in a specific directory structure that has to be provisioned by hand. The **Router** — the CPU-only born-digital fast path built to keep cheap pages off the GPU — has a known misclassification bug (issue 0001) and duplicates a decision that a better document-parsing library already makes internally.

## Solution

`docolage` becomes two things that can be reasoned about separately.

The **Core** is the existing `docolage.parse()` and everything beneath it: format normalization, page rasterization, recognition, cropping, captioning, schema assembly. It is pure Python, knows nothing about queues, HTTP, jobs or Databricks, and keeps its injectable ports. Its recognition engine changes to **docling's standard pipeline** (layout model, TableFormer for table structure, and OCR only on pages that lack a usable text layer), loaded in-process. The Router is deleted, because docling makes that call itself and makes it better.

The **Service** is a new deployable shell around the Core: an HTTP API, a Postgres-backed queue, and a pool of Workers. A user brings up four containers with `docker compose up` — `api`, `worker` (scaled to N), `postgres`, `minio` — and has a working document parser with no cloud account, no cluster, and no vendor.

The Service offers the same pipeline through two delivery contracts:

- The **Sync path** is for a human or a request waiting on an answer. The client POSTs a small document and holds the connection; pages stream back as markdown over SSE as each one finishes. Documents above the **Sync gate** are refused here and must be submitted asynchronously.
- The **Async path** is for volume. The client POSTs a document, gets a job id back immediately, and polls for status and the final result.

Both are the same queue, the same Workers, and the same parse path — only the delivery differs. Sync jobs carry a higher priority so a large async document cannot make a waiting client queue behind it.

Every page result carries **both** rendered markdown and the structured element list, so a caller who wants a markdown document and a caller who wants bboxes, confidences and crop URIs are both served from one conversion. The document-level output shape still mirrors `ai_parse_document` (ADR-0002), so existing downstream consumers keep working.

The Spark and Databricks surface — `parse_udf`, the `spark` extra, the bundle definition, the notebooks — is removed.

## User Stories

### Running the system

1. As a developer, I want to bring the whole system up with a single `docker compose up`, so that I can parse a document without provisioning any cloud infrastructure.
2. As a developer, I want the compose stack to contain only an API, Workers, Postgres and MinIO, so that there is no message broker or cache to learn, operate, or debug.
3. As a developer, I want to scale Workers with a single compose flag, so that I can add parsing throughput without changing configuration or code.
4. As a developer, I want the API to start successfully even when no Worker is running, so that submissions are accepted and queued during a Worker restart or deploy.
5. As a developer, I want database schema migrations to run automatically on API start, so that a fresh checkout works without a manual migration step.
6. As a developer, I want the object storage bucket to be created on start if it is missing, so that first run works with no manual MinIO setup.
7. As a developer, I want to point the Service at any S3-compatible endpoint through configuration, so that I can run against MinIO locally and a real object store in production without a code change.
8. As a developer, I want every setting to come from environment variables with sensible defaults, so that the same image runs locally and deployed.
9. As an operator, I want a health endpoint that reports whether Postgres and object storage are reachable, so that a container orchestrator can decide whether the API is ready.
10. As an operator, I want Workers to shut down cleanly on SIGTERM — finishing or releasing the page job they hold rather than abandoning it — so that a deploy does not strand work in a claimed state.
11. As an operator, I want structured logs carrying the job id and page number, so that I can trace a single document through the system.

### Submitting a document

12. As an API client, I want to POST a document file directly to the API and get a job created in the same request, so that submitting takes one round trip and no presign dance.
13. As an API client, I want to choose between the Sync and Async path at submit time, so that I control whether I wait for the result or collect it later.
14. As an API client, I want the API to store my upload in object storage before acknowledging it, so that an accepted submission is durable and survives an API restart.
15. As an API client, I want the API to reject a file whose format is unsupported with a clear error naming the supported formats, so that I find out immediately rather than through a failed job.
16. As an API client, I want the API to reject a file above a configured maximum size, so that a single upload cannot exhaust disk or memory.
17. As an API client, I want a corrupt or unreadable PDF to be rejected at submit time when the page count cannot be read, so that I get a synchronous error rather than a job that fails later.
18. As an API client, I want the API to determine the page count at submit time and create one queued unit of work per page, so that a document is parallelised across every available Worker.
19. As an API client, I want a submission on the Sync path above the Sync gate to be refused with an error that names the limit and tells me to use the Async path, so that the failure is actionable.
20. As an API client, I want single-page formats (PNG, Markdown) to be handled by the same submit flow as multi-page PDFs, so that I do not need format-specific client code.
21. As an API client, I want DOCX and PPTX submissions to be accepted and converted to PDF before page counting, so that office formats behave identically to PDFs from my side.
22. As an API client, I want a clear error when an office-format document is submitted but the conversion binary is unavailable on the Worker, so that a missing host dependency is diagnosable.

### The Sync path

23. As an API client, I want to hold a connection open after a sync submission and receive server-sent events, so that I get results progressively rather than after the whole document.
24. As an API client, I want each event to carry one page's markdown and its page number, so that I can render output as it arrives.
25. As an API client, I want pages to be streamed in page order regardless of the order Workers finish them, so that the streamed document reads correctly without client-side reordering.
26. As an API client, I want a terminal event telling me the job is complete along with its final status, so that I know the stream ended deliberately rather than being cut off.
27. As an API client, I want a page that failed permanently to arrive as an event carrying its error rather than silently vanishing, so that gaps in the output are explained.
28. As an API client, I want the sync stream to end with a clear timeout event if the document has not completed within a configured limit, so that a wedged job does not hold my connection forever.
29. As an API client, I want to disconnect from a sync stream without cancelling the underlying job, so that I can reconnect or fall back to polling for the same job id.
30. As an API client, I want a sync job to also be retrievable through the async status and result endpoints using its job id, so that the two contracts are two views of one job and not two systems.

### The Async path

31. As an API client, I want an async submission to return a job id immediately, so that my request does not block on parsing.
32. As an API client, I want to poll a status endpoint by job id and see the job's state, so that I know whether to keep waiting.
33. As an API client, I want the status response to include how many pages are done out of the total, so that I can show meaningful progress.
34. As an API client, I want to fetch the completed result by job id, so that I can collect output at my convenience.
35. As an API client, I want to request the result as markdown or as the structured element document, so that I can pick the representation my consumer needs.
36. As an API client, I want a request for the result of a job that is still running to return a clear not-ready response rather than a partial document, so that I never mistake incomplete output for final output.
37. As an API client, I want a request for an unknown job id to return a clear not-found error, so that client bugs are obvious.
38. As an API client, I want results to remain retrievable for a configured retention period after completion, so that I can collect them without racing a cleanup process.

### Parsing behaviour

39. As a Worker, I want to claim exactly one page job at a time in a way that no other Worker can claim it concurrently, so that a page is never parsed twice.
40. As a Worker, I want to claim jobs in priority order and then oldest-first, so that Sync jobs are served ahead of large Async batches.
41. As a Worker, I want to fetch the source document from object storage and rasterize only my own page, so that no separate splitting stage or intermediate per-page upload is needed.
42. As a Worker, I want to load the docling models once at startup and reuse them for every page, so that model load cost is paid per process rather than per page.
43. As a Worker, I want docling to decide per page whether OCR is needed, so that born-digital pages are handled correctly without a separate routing heuristic.
44. As a user, I want tables to be extracted with their structure intact, so that a table arrives as a table rather than as flattened text.
45. As a user, I want each page's result to include rendered markdown, so that I can feed output straight into a markdown-consuming tool.
46. As a user, I want each page's result to include structured elements with type, content, confidence and bounding box, so that I can build bbox-grounded citation on top.
47. As a user, I want table and figure elements to be persisted as standalone image crops with their own URIs, so that I can display the original visual alongside extracted content.
48. As a user, I want figures to be captioned via the External OpenAI API when a key is configured, so that images carry a usable description.
49. As a user, I want the system to run correctly with no OpenAI key configured, producing every element type with figures simply carrying no description, so that the external dependency is genuinely optional.
50. As a user, I want a figure captioning failure to leave the figure without a description rather than failing the page, so that an external API outage degrades output instead of breaking it.
51. As a user, I want the final document result to follow the same schema the Databricks version produced, so that consumers written against it keep working.
52. As a user, I want pages assembled into the final document in page order, so that the document reads correctly end to end.

### Failure and retry

53. As a Worker, I want a page job that fails with a transient error to be retried up to a configured limit, so that a blip does not lose a page.
54. As a Worker, I want a page job claimed by a Worker that died to become claimable again after a lease expires, so that a crashed Worker does not strand a page forever.
55. As a user, I want a page that has exhausted its retries to be recorded as failed with its error message and page number, so that the failure is visible and diagnosable.
56. As a user, I want a job with at least one permanently-failed page and at least one successful page to complete as partial rather than failed, so that a single bad page does not discard the work spent on every other page.
57. As a user, I want a partial job's result to contain every successful page plus an error entry per failed page, so that I can decide whether the output is usable.
58. As a user, I want a job whose every page failed to be reported as failed rather than partial, so that a completely unusable result is not presented as a success.
59. As a user, I want the job's status to distinguish queued, running, completed, partial and failed, so that I can act differently on each.
60. As an operator, I want a job to be marked failed if it has made no progress for a configured period, so that wedged jobs do not accumulate as permanently-running.

### Migration off Databricks

61. As a maintainer, I want the Spark batch entry point and its optional dependency removed, so that the Core has one entry point and no Spark-shaped constraints on its signature.
62. As a maintainer, I want the Databricks bundle definition and notebooks removed, so that nothing in the repo implies a Databricks deployment that is no longer supported.
63. As a maintainer, I want UC Volume paths replaced by object storage URIs throughout configuration, so that there is no vendor-specific path handling left.
64. As a maintainer, I want the PaddleOCR recognizer and its optional dependency removed once the docling recognizer is in place, so that there is one recognition engine to maintain rather than two.
65. As a maintainer, I want the Router and its tests removed, so that the born-digital misclassification bug in issue 0001 is closed by deletion.
66. As a maintainer, I want the Service to live in its own package that depends on the Core, so that installing the Core never pulls in a web framework or a database driver.
67. As a maintainer, I want the Core to remain importable and usable as a plain library function with no Service running, so that a single-document script stays a one-liner.

## Implementation Decisions

### Package boundary

The Service lives in a **separate top-level package** in the same repository, depending on the Core package. The dependency direction is one-way and enforced by the package boundary: the Core must never import queue, API, or storage code. Practically this keeps a plain Core install free of the web framework, the database driver, and the object storage client.

The Core keeps its existing public seam — a single `parse` function taking a path and an explicit config object, with ports injected through that config. The Service calls it once per page.

### Recognition engine (ADR-0010)

The **Recognizer** port is retained unchanged in shape. A new implementation wraps docling's standard pipeline and is selected through config exactly as the previous recognizer was. Docling models are loaded once per Worker process and reused.

The Core's per-page recognition contract stays as-is: given a rasterized page image and a page number, return layout elements in reading order. Docling's page output is mapped into that element shape, plus rendered markdown for the page. Because the unit of work is one page, docling never sees the whole document, so cross-page reading order, section hierarchy across pages, and tables continued across a page break are not resolved — this is accepted and recorded in ADR-0009.

The **Router** is deleted along with its born-digital heuristic and the text-layer extraction path. Docling decides per page whether OCR is required. There is consequently no CPU-only path: every page pays layout inference.

Figure captioning through the **Figure captioner** port becomes **unconditional** — every figure is captioned when a key is configured, and carries no description when one is not. The confidence threshold and the escalation concept are removed, because docling produces no per-figure description or confidence to escalate from. Captioner failures are swallowed per figure, leaving the figure without a description rather than failing the page.

### Storage and queue (ADR-0007)

Postgres holds job metadata, the page-job queue, and completed page results. Object storage holds uploads, page images, and element crops. There is no other infrastructure.

The job record carries at minimum: identity, delivery mode (sync or async), priority, source object URI, original filename and format, total page count, status, timestamps, and an error summary. The page-job record carries: owning job, page number, status, attempt count, claim lease expiry, and its error message when failed. The page-result record carries: owning job, page number, rendered markdown, and the structured element list. Page results are stored per page rather than only as an assembled document, because the Sync path needs to read them one at a time as they land.

Workers claim work with a single atomic statement that selects the highest-priority oldest unclaimed page job, skipping rows locked by other Workers, and marks it claimed with a lease expiry in the same transaction. Claims that outlive their lease become claimable again, which is what makes a crashed Worker recoverable. Attempt count is incremented on claim so that retries are bounded even when the failure is a crash rather than an exception.

Job status is derived from its page jobs: completed when all pages succeeded, partial when at least one succeeded and at least one permanently failed, failed when none succeeded.

### API contract

The API exposes, at minimum: submit (multipart upload, with delivery mode and priority implied by mode), a sync stream for a job, job status by id, and job result by id with a representation selector for markdown or structured elements. Health is a separate endpoint reporting Postgres and object storage reachability.

Submit performs, in order: validate format and size, write the upload to object storage, read the page count, and insert the job and its page jobs in one transaction. Rasterization is deliberately *not* done at submit — a Worker rasterizes its own page from the source document. This removes the splitter stage the reference architecture drew, avoids writing page images for jobs that fail early, and keeps submit fast enough that a large async upload does not occupy an API worker for long.

### Sync delivery (ADR-0008)

The sync stream is implemented by **polling the page-result table** on a short interval and emitting newly-completed pages in page order, tracking a high-water mark per stream. Postgres LISTEN/NOTIFY was rejected: it needs a dedicated connection or an in-process fanout, and a reconnecting client would still have to backfill from the table, so the table remains the source of truth either way. The cost is bounded added latency per page and one cheap indexed query per open stream.

Disconnecting from a stream does not cancel the job. The stream terminates on job completion, on a configured wall-clock timeout, or on client disconnect.

Sync and async are **one pipeline** distinguished only by delivery. Priority ordering is the entire mechanism preventing a large async document from starving a waiting sync client; there are no reserved sync Workers in this iteration.

### Deployment

A compose file defines `api`, `worker`, `postgres` and `minio`. Worker replica count is the scaling knob and is also the GPU count, since models are in-process. Migrations run on API start; the object storage bucket is created if absent.

### Deferred, with the seam left in place

Content-hash dedupe and a result cache are not built. A vLLM-served recognizer is not built, but the Recognizer port is the seam that would accept it. Presigned direct-to-storage upload is not built. Reserved sync Workers are not built.

### Unresolved, pending measurement

The **Sync gate** threshold (maximum pages accepted on the Sync path) and the **per-Worker concurrency** (pages in flight per Worker, bounded by model VRAM) are both functions of docling's measured per-page latency and memory use on the target GPU. Both must be configurable, with initial defaults set from a benchmark run over the existing sample documents rather than guessed. The existing benchmark harness and sample PDFs are the measurement vehicle.

## Testing Decisions

A good test here asserts only on externally visible behaviour. For the Core, that means the returned document — element types, content, confidence, bboxes, crop URIs, page order, error entries — never on which internal function was called. For the Service, that means what an HTTP client observes — response codes, job status transitions, streamed events, final result documents — never on queue internals, table contents, or worker internals. No test may reach into the queue module directly; if a queue behaviour matters, it must be observable through the API.

**Two seams, one of them existing.**

**Seam 1 — the Core, unchanged.** Tests drive the existing `parse` function with fakes injected through the config object, exactly as the current suite does. Prior art is the existing VLM-path, element-crop, figure, error-isolation, and office-format tests; the existing recognizer fake — which already supports per-page canned elements and per-page injected failures — remains the mechanism for driving both success and page-level failure. Docling's real recognizer gets only a smoke test marked so it is excluded from the default run, following the existing GPU-marked smoke test.

Core tests to update rather than add: everything asserting Router behaviour or the born-digital text-layer path is deleted with the Router; the figure tests change from asserting a confidence threshold to asserting that every figure is captioned when a captioner is configured and none are when it is not.

**Seam 2 — the Service, new, at the HTTP API.** Tests drive the API through an in-process ASGI client, with a real Worker loop running against a real throwaway Postgres and a real object store, and the Core's ports faked so no GPU or network is involved. This one seam covers submit, page-job creation, claiming, parsing, reassembly, and both delivery contracts.

Real infrastructure rather than a fake queue is a deliberate choice: atomic claiming, priority ordering, lease expiry, and concurrent-Worker safety are the only interesting properties of the queue, and a fake would test none of them. These tests are gated behind a marker that skips cleanly when the containers are not up, matching the existing honesty pattern — the GPU marker, and the skip-if-unimportable treatment of pyspark and the office-conversion binary. The default test run must stay green on a laptop with nothing running.

Behaviours worth a Service test, at minimum: a sync submission streams pages in page order and terminates with a completion event; a submission over the sync gate is refused with an actionable error; an async submission returns a job id and reaches completion via polling; a job id is usable across both contracts; a single permanently-failed page yields a partial job containing every other page plus an error entry; a job with all pages failed is reported failed; two Workers running concurrently never parse the same page twice; a higher-priority job queued behind a large lower-priority one is served first; a claim whose lease expired is re-claimable; retries are bounded by the configured limit; an unsupported format and an oversized file are rejected at submit; an unknown job id returns not-found; a result requested before completion returns not-ready rather than a partial document.

## Out of Scope

Chunking and any downstream RAG-prep enhancement remain out of scope, unchanged from the previous spec — the pipeline's job ends at the parse result.

Also out of scope for this spec: authentication, authorization, and multi-tenancy on the API; rate limiting and quota; a Kubernetes deployment and queue-depth autoscaling; a vLLM or any other model-serving tier; content-hash dedupe and result caching; presigned direct-to-storage upload; reserved sync Worker pools; a web UI; a client SDK; cross-page structure reconciliation during reassembly; and any new document format beyond those already supported.

Databricks support is not merely out of scope but actively removed. There is no migration path provided for existing `parse_udf` users beyond moving to the Service.

## Further Notes

The architecture this spec describes is a deliberate simplification of a reference design that had a separate sync pipeline and async pipeline, Kafka, RabbitMQ, Redis, a splitter service, and a vLLM model-serving tier. Every one of those was removed or collapsed for the MVP, and each removal is recorded with its trade-off in ADR-0007 through ADR-0011. The seams that would let them come back — the Recognizer port for a serving tier, the queue module for a broker, the priority column for physically separated pools — are deliberately preserved. A reader who wonders why this looks smaller than the diagram should start with those ADRs.

Issue 0001 (born-digital Router misses) is closed by the deletion of the Router rather than by a fix.

The largest known quality risk is per-page isolation from docling: because a Worker parses one page with no knowledge of its neighbours, a table split across a page break and section hierarchy spanning pages are not reconciled. If measured output quality suffers, the intended remedy is to move the unit of work from one page to a batch of N pages, which is contained to the Service's job-creation and reassembly logic and requires no Core change.
