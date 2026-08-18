"""PNG format handler: a standalone image is a single already-rasterized
page. Same VLM path as a scanned PDF page, minus rasterization."""

from __future__ import annotations

from pathlib import Path

from docolage import schema
from docolage.config import DocolageConfig, default_recognizer
from docolage.captioning import caption_figures
from docolage.crop import CROPPABLE_TYPES, crop_element

_PAGE_ID = 1


def parse(path: Path, config: DocolageConfig) -> dict:
    recognizer = config.vlm_recognizer if config.vlm_recognizer is not None else default_recognizer()

    image_uri = str(path)
    page = schema.make_page(_PAGE_ID, image_uri)
    pages = [page]
    output_dir = Path(config.output_path)

    elements: list[dict] = []
    error_status: list[dict] = []
    try:
        result = recognizer.recognize(image_uri, _PAGE_ID)
        for i, le in enumerate(result.elements, start=1):
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
        page["markdown"] = result.markdown
    except Exception as exc:  # per-page isolation, ADR-0006
        error_status.append({"error_message": str(exc), "page_id": _PAGE_ID})

    elements = caption_figures(elements, config.figure_captioner)

    return schema.build_document(pages=pages, elements=elements, error_status=error_status)
