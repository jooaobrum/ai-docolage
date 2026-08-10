"""PNG format handler: a standalone image is a single already-rasterized
page. Same VLM path as a scanned PDF page, minus rasterization."""

from __future__ import annotations

from pathlib import Path

from docolage import schema
from docolage.config import DocolageConfig
from docolage.crop import CROPPABLE_TYPES, crop_element
from docolage.escalation import apply_escalation

_PAGE_ID = 1


def parse(path: Path, config: DocolageConfig) -> dict:
    if config.vlm_recognizer is None:
        raise RuntimeError("Parsing a PNG requires a vlm_recognizer configured on DocolageConfig.")

    image_uri = str(path)
    pages = [schema.make_page(_PAGE_ID, image_uri)]
    output_dir = Path(config.output_volume_path)

    elements: list[dict] = []
    error_status: list[dict] = []
    try:
        layout_elements = config.vlm_recognizer.recognize(image_uri, _PAGE_ID)
        for i, le in enumerate(layout_elements, start=1):
            element_image_uri = ""
            if le.type in CROPPABLE_TYPES:
                element_image_uri = crop_element(image_uri, le.coord, output_dir, path.stem, _PAGE_ID, i)
            elements.append(
                schema.make_element(
                    element_id=i,
                    element_type=le.type,
                    content=le.content,
                    confidence=le.confidence,
                    bbox=[schema.make_bbox(_PAGE_ID, le.coord)],
                    description=le.description,
                    image_uri=element_image_uri,
                )
            )
    except Exception as exc:  # per-page isolation, ADR-0006
        error_status.append({"error_message": str(exc), "page_id": _PAGE_ID})

    elements = apply_escalation(elements, config.figure_captioner, config.escalation_threshold)

    return schema.build_document(pages=pages, elements=elements, error_status=error_status)
