"""PDF format handler: rasterize, Router, born-digital text, VLM path.

Every page rasterizes to a PNG first (image_uri comes from that for
free). The Router then decides, per page and on born-digital status
alone, between direct text-layer extraction (no GPU) and the VLM
recognizer port. A table spanning a page break is merged into one
element afterwards.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

from docolage import schema
from docolage.config import DocolageConfig
from docolage.crop import CROPPABLE_TYPES, crop_element
from docolage.rasterize import rasterize_page
from docolage.router import is_born_digital
from docolage.table_merge import merge_table_html

_POINTS_PER_INCH = 72


def _extract_born_digital_elements(page, page_id: int, scale: float) -> list[dict]:
    textpage = page.get_textpage()
    _, page_height = page.get_size()
    elements = []
    try:
        for i in range(textpage.count_rects()):
            left, bottom, right, top = textpage.get_rect(i)
            text = textpage.get_text_bounded(left, bottom, right, top).strip()
            if not text:
                continue
            pixel_coord = [
                round(left * scale),
                round((page_height - top) * scale),
                round(right * scale),
                round((page_height - bottom) * scale),
            ]
            elements.append(
                {
                    "type": "text",
                    "content": text,
                    "confidence": 1.0,
                    "description": "",
                    "bbox": [{"page_id": page_id, "coord": pixel_coord}],
                }
            )
    finally:
        textpage.close()
    return elements


def _extract_vlm_elements(vlm_recognizer, image_path: str, page_id: int) -> list[dict]:
    layout_elements = vlm_recognizer.recognize(image_path, page_id)
    return [
        {
            "type": le.type,
            "content": le.content,
            "confidence": le.confidence,
            "description": le.description,
            "bbox": [{"page_id": page_id, "coord": le.coord}],
        }
        for le in layout_elements
    ]


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
    scale = config.dpi / _POINTS_PER_INCH
    output_dir = Path(config.output_volume_path)

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
            pages.append(schema.make_page(page_id, image_path))

            page = pdf[page_index]
            if is_born_digital(page):
                raw_elements.extend(_extract_born_digital_elements(page, page_id, scale))
            else:
                if config.vlm_recognizer is None:
                    raise RuntimeError(
                        "Page has no embedded text layer and no vlm_recognizer is "
                        "configured on DocolageConfig."
                    )
                raw_elements.extend(
                    _extract_vlm_elements(config.vlm_recognizer, image_path, page_id)
                )
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

    return schema.build_document(pages=pages, elements=elements, error_status=error_status)
