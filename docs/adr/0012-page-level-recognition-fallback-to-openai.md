---
status: accepted
---

# Page-level recognition fallback to OpenAI on low docling confidence

ADR-0010 removed per-figure escalation (ADR-0003) because docling's standard pipeline
doesn't expose a per-figure confidence, but it explicitly left the door open: "a cheap
pre-filter can be reintroduced later behind the same port." It also kept a page-level
signal that ADR-0010 didn't use for anything: docling's own confidence report
(`mean_score`, rolling up parse/layout/table/OCR sub-scores), which
`DoclingRecognizer` already returns per page (`recognizer_docling.py`'s
`_page_confidence`).

We reintroduce escalation at that page level, reusing the OpenAI dependency already
wired for figure captioning rather than adding a second external dependency. A new
`FallbackRecognizer` composes `DoclingRecognizer` (primary) with `OpenAIRecognizer`
(fallback), both implementations of the existing `VLMRecognizer` port: below
`recognition.confidence_threshold` (docolage.yaml), the whole page is re-recognized via
the OpenAI API instead of docling's result. With no `OPENAI_API_KEY` configured,
`build_openai_recognizer()` returns `None` and the fallback never runs — same
degrade-to-nothing posture as the Figure captioner.

**Threshold, tuned against real data.** Rather than guess, we queried the 478 pages
already in `page_results` for docling's confidence distribution: mean 0.938, p50 0.950,
min 0.7408. `confidence_threshold: 0.85` was chosen because it catches the genuine long
tail (23 pages, ~5%) without dragging in the median "slightly imperfect" pages at 0.90+
(which would triple OpenAI spend for pages that looked fine on spot check).

**Spot-checking one page (189 of stmicro-financial-report.pdf, confidence 0.7408 —
rotated footnote text) surfaced two real risks, both closed here, not deferred:**

1. The OpenAI recognizer's `markdown` field, originally requested as a second,
   independent model output, could disagree with its own `elements` (observed: elements
   populated, markdown empty). `markdown` is now derived by joining `elements`' content
   instead, guaranteeing consistency by construction — the same convention
   `FakeVLMRecognizer` already used.
2. `table_merge.py` merges a table split across a page break by regexing for
   `<tbody>...</tbody>`; a table whose `content` didn't have that exact shape silently
   degraded to raw string concatenation (corrupted table), not an error. The OpenAI
   recognizer's prompt now explicitly requires `<tbody>`-wrapped HTML for `type="table"`,
   matching docling's `export_to_html()` convention, so a table spanning a docling/fallback
   boundary merges correctly.

**The same spot check also showed the fallback can fail quietly.** `gpt-4o-mini`
confabulated a plausible but wrong defined term for text it couldn't actually resolve —
differently on each retry — while returning fluent, well-formed JSON. Unlike docling's
visibly garbled failure, nothing downstream would flag this as suspect. Elements from
`OpenAIRecognizer` therefore do not claim full confidence: they carry a fixed, deliberately
conservative `openai.fallback_result_confidence` (0.6, below `recognition_confidence_threshold`,
docolage.yaml) rather than 1.0 — "the call succeeded" is not "the content is correct."
A follow-up spot check with a stronger model configured via `openai.model`
(`gpt-5.6-luna`) resolved the same page correctly and consistently across three runs,
suggesting model choice matters more here than any prompt tuning could; `openai.model` is
shared with the Figure captioner (`openai_client.py`), and both ports also accept an
explicit `model=` override at the call site if they ever need to diverge.

**Consequence, accepted rather than fixed here.** `FallbackRecognizer` operates per page,
so a single document's pages can be a mix of docling- and OpenAI-recognized output.
`reassembly.py` and `schema.py` are format-agnostic enough that this never crashes, but a
`type="table"` element whose two halves come from different recognizers (one docling, one
OpenAI) is only merged correctly because both are now constrained to the same HTML shape
(point 2 above) — a recognizer that drifted from that convention in the future would
silently corrupt the merged table rather than error.
