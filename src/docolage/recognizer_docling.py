"""Real VLMRecognizer backed by docling's standard pipeline (ADR-0010).

docling's own API is document-oriented: ``DocumentConverter.convert()``
takes a whole PDF (or other document) and returns one ``DoclingDocument``
with reading order, section hierarchy and continued tables resolved
across the whole thing. The Core's ``Recognizer``/``VLMRecognizer`` port
(``docolage.ports``), by contrast, is called once per already-rasterized
page (ADR-0009 "the page is the unit of work") — the Core never hands
docling a whole document, on purpose.

To reconcile the two, this module wraps each incoming page PNG in a
throwaway **single-page, in-memory PDF** (via Pillow, already a Core
dependency) and feeds that to docling as a ``DocumentStream``. This is
approach (a) from the ticket: construct a docling source around just
that one page's image, rather than trying to persuade docling's
document-level API to process a "batch of one" some other way. The
resulting PDF page is sized in points 1:1 with the source image's pixel
dimensions (``Image.save(..., format="PDF", resolution=72.0)`` places
the PDF page boundary exactly at the image's pixel size), so every box
docling returns is already in the same pixel space as the rasterized
page PNG the rest of the Core operates on — no extra DPI rescale needed
between docling's point space and this package's pixel space.

Because each call sees a single, isolated page, docling's own
document-level reasoning (cross-page reading order, section hierarchy,
tables continued across a page break) never fires — consistent with
ADR-0009, which accepts that loss and leaves ``table_merge.py`` to
reconcile page-break tables afterwards, one layer up in ``formats/pdf.py``.

Docling's standard pipeline decides internally, per page, whether OCR is
needed (a usable embedded text layer skips OCR) — this recognizer does
not implement or duplicate that decision; it is exactly the CPU-only
Router heuristic ADR-0010 replaced. The Router and the born-digital
text-layer path are deleted (ticket #15); every page now goes through
this recognizer (or another ``VLMRecognizer`` implementation)
unconditionally, and this recognizer is docling's default.
"""

from __future__ import annotations

import io
import math
from typing import Any

from docolage.ports import LayoutElement, RecognitionResult

_LABEL_TYPE_MAP: dict[str, str] = {
    "title": "heading-1",
    "section_header": "heading-1",
    "table": "table",
    "picture": "figure",
    "chart": "figure",
    "formula": "formula",
    "page_header": "header",
    "page_footer": "footer",
}

def _map_label(label: Any) -> str:
    """DocItemLabel -> this package's element ``type`` vocabulary.

    Anything not explicitly listed (paragraph, text, caption, footnote,
    list_item, code, reference, document_index, checkbox_*,
    handwritten_text, field_*, form, key_value_region, grading_scale,
    marker, empty_value, ...) maps to plain "text" so unrecognized
    labels degrade gracefully rather than raising.
    """
    value = getattr(label, "value", label)
    return _LABEL_TYPE_MAP.get(str(value), "text")

class DoclingRecognizer:
    """Real ``VLMRecognizer`` (docolage.ports) backed by docling's standard
    pipeline (layout model + TableFormer + OCR-when-needed).

    Models are constructed once, at ``__init__``, and reused for every
    ``recognize()`` call on this instance: ``docling.DocumentConverter``
    caches an initialized pipeline per (format, pipeline-options) key, and
    ``initialize_pipeline()`` forces that cache to warm eagerly rather than
    lazily on first use, matching the spec's "Workers load the docling
    models once at startup and reuse them for every page."
    """

    def __init__(self) -> None:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise ImportError(
                "DoclingRecognizer requires the 'docling' package, which is not "
                "installed in this environment. Install it (it is a base "
                "dependency of docolage per ADR-0010) before selecting "
                "DoclingRecognizer via DocolageConfig.vlm_recognizer."
            ) from exc

        self._input_format = InputFormat.PDF
        self._converter = DocumentConverter(allowed_formats=[InputFormat.PDF])
        self._converter.initialize_pipeline(self._input_format)

    def recognize(self, image_path: str, page_id: int) -> RecognitionResult:
        """Run one rasterized page PNG through docling's standard pipeline.

        Wraps the page image as a single-page in-memory PDF (see module
        docstring), converts it, and maps the resulting ``DoclingDocument``
        into ``LayoutElement``s in docling's own reading order, alongside
        that same ``DoclingDocument``'s built-in ``export_to_markdown()``
        rendering (spec user stories 45/46, ticket #16) — docling already
        builds this document per page for element mapping; markdown export
        just reads it a second way rather than discarding it.
        """
        from docling_core.types.doc import PictureItem, TableItem
        from docling_core.types.io import DocumentStream

        stream = DocumentStream(name="page.pdf", stream=io.BytesIO(_page_png_to_pdf_bytes(image_path)))
        result = self._converter.convert(stream, raises_on_error=False)

        document = result.document
        page_confidence = _page_confidence(result)

        elements: list[LayoutElement] = []
        for item, _level in document.iterate_items(with_groups=False):
            prov = getattr(item, "prov", None)
            if not prov:
                continue

            box = prov[0].bbox.to_top_left_origin(document.pages[prov[0].page_no].size.height)
            coord = [round(box.l), round(box.t), round(box.r), round(box.b)]

            if isinstance(item, TableItem):
                element_type = "table"
                content = item.export_to_html(doc=document)
                description = ""
            elif isinstance(item, PictureItem):
                element_type = "figure"
                content = ""
                description = ""
            else:
                element_type = _map_label(getattr(item, "label", "text"))
                content = getattr(item, "text", "") or ""
                description = ""

            elements.append(
                LayoutElement(
                    type=element_type,
                    content=content,
                    confidence=page_confidence,
                    coord=coord,
                    description=description,
                )
            )

        markdown = document.export_to_markdown()
        return RecognitionResult(elements=elements, markdown=markdown)

def _page_png_to_pdf_bytes(image_path: str) -> bytes:
    """Wrap one rasterized page PNG as a single-page PDF, in memory.

    ``resolution=72.0`` is load-bearing: it makes the emitted PDF page's
    point-space size equal the source image's pixel dimensions, so
    docling's bbox output lands directly in this package's pixel space
    with no further scaling.
    """
    from PIL import Image

    with Image.open(image_path) as image:
        rgb_image = image.convert("RGB")
        buffer = io.BytesIO()
        rgb_image.save(buffer, format="PDF", resolution=72.0)
        return buffer.getvalue()

def _page_confidence(result: Any) -> float:
    """Best-effort confidence for every element on the page.

    Docling's standard pipeline emits a per-page confidence report (parse,
    layout, table and OCR sub-scores rolled into a ``mean_score``), not a
    per-element score — there is nothing more granular to attach to an
    individual ``LayoutElement``, so every element on a page shares that
    page's score, defaulting to 1.0 ("no usable signal") when the report is
    absent or NaN.
    """
    pages = getattr(getattr(result, "confidence", None), "pages", None)
    if not pages:
        return 1.0
    scores = [p.mean_score for p in pages.values() if not math.isnan(p.mean_score)]
    if not scores:
        return 1.0
    return float(sum(scores) / len(scores))
