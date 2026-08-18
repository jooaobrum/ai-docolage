---
status: accepted
---

# The page is the unit of work, and cross-page structure is given up to get it

A queued job could be a whole document, a batch of N pages, or a single page. Docling's own pipeline is document-oriented — hand it a PDF and it returns one `DoclingDocument` with reading order, section hierarchy and continued tables resolved across page boundaries — so whole-document jobs would give the best output quality and the simplest reassembly.

We chose **one page = one job** anyway. It parallelises across Workers, it keeps ADR-0006's per-page error isolation as a property of the queue rather than of a loop inside one worker, it lets the Sync path stream results progressively instead of after the last page, and it stops a 200-page document from pinning a single Worker (and single GPU) for minutes. It also matches the existing Recognizer port, which is already per-page, so the Core does not change shape.

**Consequences, accepted deliberately.** Docling never sees more than one page at a time, so cross-page reading order, section hierarchy spanning pages, and tables continued across a page break are lost — a **Reassembly** step orders page results back into one document result by page number and concatenates, without reconciling structure across the seam. This is the main quality cost of the architecture and the first thing to revisit if output quality disappoints. A page-batch unit of work (N pages per job) is the natural middle ground if it does.

A permanently-failed page yields a **partial** job: every other page is still delivered, with the failed page recorded in `error_status` per ADR-0006. One bad page never discards the GPU work already spent on the rest of the document.
