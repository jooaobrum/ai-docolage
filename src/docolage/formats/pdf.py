"""PDF format handler: rasterize, then run every page through the
configured Recognizer.

Every page rasterizes to a PNG first (image_uri comes from that for
free). The Recognizer (docolage.ports.VLMRecognizer) then decides,
internally and per page, whether OCR is needed — there is no separate
CPU-only text-layer path (ADR-0010: the Router is deleted). A table
spanning a page break is merged into one element afterwards.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

from docolage import schema
from docolage.captioning import caption_figures
from docolage.config import DocolageConfig, default_recognizer
from docolage.crop import CROPPABLE_TYPES, crop_element
from docolage.rasterize import rasterize_page
from docolage.table_merge import merge_table_html


def _extract_vlm_elements(vlm_recognizer, image_path: str, page_id: int) -> tuple[list[dict], str]:
    """Return this page's structured elements and its rendered markdown
    (ticket #16) — both come out of one Recognizer call."""
    result = vlm_recognizer.recognize(image_path, page_id)
    elements = [
        {
            "type": le.type,
            "content": le.content,
            "confidence": le.confidence,
            "description": le.description,
            "bbox": [{"page_id": page_id, "coord": le.coord}],
        }
        for le in result.elements
    ]
    return elements, result.markdown


def _merge_page_break_tables(raw_elements: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for element in raw_elements:
        if (
            merged
            and merged[-1]["type"] == "table"
            and element["type"] == "table"
            and merged[-1]["bbox"][-1]["page_id"] != element["bbox"][0]["page_id"]
        ):
            previous = merged[-1]
            previous["content"] = merge_table_html(previous["content"], element["content"])
            previous["bbox"] = previous["bbox"] + element["bbox"]
            previous["confidence"] = min(previous["confidence"], element["confidence"])
        else:
            merged.append(element)
    return merged


def parse(path: Path, config: DocolageConfig) -> dict:
    pdf = pdfium.PdfDocument(str(path))
    output_dir = Path(config.output_path)

    pages = []
    raw_elements: list[dict] = []
    error_status: list[dict] = []

    for page_index in range(len(pdf)):
        page_id = page_index + 1

        # Rendering and recognition share one try/except per page (ADR-0006):
        # a corrupt render, a GPU OOM, or a recognizer timeout are all
        # reported the same way, and never abort sibling pages that already
        # cost GPU time.
        try:
            image_path = rasterize_page(pdf, page_index, output_dir, path.stem, config.dpi)
            page = schema.make_page(page_id, image_path)
            pages.append(page)

            recognizer = config.vlm_recognizer if config.vlm_recognizer is not None else default_recognizer()
            page_elements, page_markdown = _extract_vlm_elements(recognizer, image_path, page_id)
            raw_elements.extend(page_elements)
            page["markdown"] = page_markdown
        except Exception as exc:  # per-page isolation, ADR-0006
            error_status.append({"error_message": str(exc), "page_id": page_id})

    raw_elements = _merge_page_break_tables(raw_elements)
    page_image_paths = {page["id"]: page["image_uri"] for page in pages}

    elements = []
    for i, e in enumerate(raw_elements, start=1):
        image_uri = ""
        if e["type"] in CROPPABLE_TYPES:
            first_bbox = e["bbox"][0]
            page_image_path = page_image_paths.get(first_bbox["page_id"])
            if page_image_path is not None:
                image_uri = crop_element(
                    page_image_path, first_bbox["coord"], output_dir, path.stem, first_bbox["page_id"], i
                )
        elements.append(
            schema.make_element(
                element_id=i,
                element_type=e["type"],
                content=e["content"],
                confidence=e["confidence"],
                bbox=e["bbox"],
                description=e["description"],
                image_uri=image_uri,
            )
        )

    elements = caption_figures(elements, config.figure_captioner)

    return schema.build_document(pages=pages, elements=elements, error_status=error_status)
