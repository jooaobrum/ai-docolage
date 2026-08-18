**Status: closed — see ADR-0010 and ticket #15.** The Router (and its born-digital text-layer path) has been deleted rather than fixed; docling's standard pipeline now decides per page, internally, whether OCR is needed, closing both gaps described below by removal.

# Issue 0001: Router's born-digital check lets bad text through and misses embedded figures/tables

Found while building `benchmark/` and running `benchmark/eval.py` against the 4 documents in
`data/`. Aggregate score: 0.688/1.0 across 4 docs, driven by two related gaps in
`src/docolage/router.py` / `src/docolage/formats/pdf.py`.

## Bug: `is_born_digital()` accepts garbled/undecodable text

`is_born_digital()` (`src/docolage/router.py:12`) only checks
`textpage.count_chars() > 0` — it confirms glyphs exist, not that they decode to
meaningful text.

`data/stmicro-financial-report.pdf` uses a non-standard font encoding. pypdfium2's raw
text extraction on its early pages returns cipher-like garbage, e.g.:

```
N@AhYdXjbZciAhi@ZJ>#,egAciZYkZghAdcd>i@Z/-/2;ccjVaLZedgid>MNGAXgdZaZXigdcAXhH+P+VcY@Vh
```

(decodes to something like "This document is the PDF-printed version of the 2025 Annual
Report of STMicroelectronics N.V. and has ...")

`is_born_digital()` returns `True` for these pages (confirmed via direct call), so they
route to `_extract_born_digital_elements()` (`src/docolage/formats/pdf.py:26`) and the
garbage text is emitted as-is, instead of falling back to the VLM/OCR path which reads the
rasterized page image and would recover the real text.

**Impact**: `text_hit_rate` for `stmicro-financial-report` in `benchmark/eval.py` is 0.00 —
none of the expected cover-page text ("STMicroelectronics N.V.", "Annual Report", "2025",
all visually confirmed present) is recoverable from the parser's output.

**Suggested direction**: add a text-quality heuristic to the born-digital check — e.g. ratio
of printable/expected-alphabet characters, or a quick dictionary/language check — and route
pages that fail it to the VLM path even though `count_chars() > 0`.

## Design gap: born-digital pages never emit `table`/`figure` elements

Separate from the bug above: the Router's docstring already documents this tradeoff
("Detecting tables or figures cheaply enough to matter would require running layout
inference, defeating the point of a CPU-only path") — flagging it here because it produces
a real, measurable quality gap, not because it's unintentional.

Once a page is classified born-digital, `_extract_born_digital_elements()` only ever emits
`type: "text"` from the raw text layer — no layout/figure/table detection runs on that path.
Only pages that fail the born-digital check reach `_extract_vlm_elements()`, the only code
path capable of emitting `table`/`figure` types.

**Impact** (measured via `benchmark/eval.py` against `benchmark/ground_truth/`):
- `data/TSMCJ.PDF`: page 2 alone has 6 characteristic-curve figures (Fig.1–Fig.6) and a
  packaging table, visually confirmed by rendering the page — parser emits 0 figures, 0
  tables.
- `data/paper.pdf`: text layer references 15 distinct captioned figures (Figure 1.–Figure
  15.) — parser emits 0 figures.

**Suggested direction**: either (a) accept this as a documented limitation and communicate
it clearly to users of born-digital-heavy corpora, or (b) add a cheap embedded-image/XObject
check per page (pypdfium2 exposes this without running full layout inference) to route pages
with embedded raster images through the VLM path even when text is also present.

## Reproduction

```
cd ai-docolage
python3 benchmark/eval.py
```

See `benchmark/manifest.json`, `benchmark/ground_truth/*.json`, and `benchmark/eval.py` for
the full benchmark this was found with.
