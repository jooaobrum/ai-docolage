---
status: accepted
---

# Docling's standard pipeline, loaded in-process in the Worker — no model-serving tier

Supersedes ADR-0001, ADR-0003 and ADR-0005, all of which were written around PaddleOCR-VL on Databricks GPU compute.

Docling offers two pipelines. The **standard** pipeline composes a layout model, TableFormer for table structure, and an OCR engine invoked only on pages without a usable text layer. The **VLM** pipeline emits DocTags for a whole page from a single vision model, and is the one that can be served by vLLM over HTTP — which is what the reference architecture drew, and what would let GPU capacity scale independently of Worker count.

We chose the **standard pipeline, loaded in-process in each Worker**. Table extraction is the hardest part of this problem and TableFormer is materially better at it than a small general VLM; running in-process removes an entire service, an HTTP hop, and a second scaling dimension from the MVP. Worker count is therefore also GPU count. This is deliberately the same in-process posture as ADR-0001, arrived at again for different reasons, and the **Recognizer** port is retained precisely so a vLLM-backed implementation can replace it later without the Core knowing.

**Consequences.**

The **Router** is deleted. Its job was to keep born-digital pages off the GPU by reading the embedded text layer on CPU; docling's standard pipeline already makes that decision internally, per page, and better. Keeping ours would mean two extractors producing two element shapes and duplicated heuristics — along with the misclassification bug in issue 0001, which dies with the Router. The cost is that there is no longer a truly CPU-only fast path: every page pays layout inference.

**Figure captioning becomes unconditional.** ADR-0003 escalated to OpenAI only for figures whose VLM-produced description was empty or low-confidence. Docling returns figures with no description and no per-figure confidence to threshold on, so there is nothing to escalate *from*. We keep our own **Figure captioner** port and the OpenAI implementation, but the rule is now: caption every figure if a key is configured, and carry no description if one is not. We rejected docling's built-in picture-description enrichment to retain control over prompt, cost and per-figure skipping. Cost per document rises relative to the escalation design; a cheap pre-filter can be reintroduced later behind the same port.

ADR-0002 (mirroring `ai_parse_document`'s output shape) and ADR-0006 (per-page error isolation) survive unchanged — docling's output is mapped into that schema, and each page result additionally carries markdown.
