"""PNG format handler: a standalone image is a single already-rasterized
page. Same VLM path as a scanned PDF page, minus rasterization."""

from __future__ import annotations

from pathlib import Path

from docolage import schema
from docolage.config import DocolageConfig

_PAGE_ID = 1


def parse(path: Path, config: DocolageConfig) -> dict:
    if config.vlm_recognizer is None:
        raise RuntimeError("Parsing a PNG requires a vlm_recognizer configured on DocolageConfig.")

    image_uri = str(path)
    pages = [schema.make_page(_PAGE_ID, image_uri)]

    layout_elements = config.vlm_recognizer.recognize(image_uri, _PAGE_ID)
    elements = [
        schema.make_element(
            element_id=i,
            element_type=le.type,
            content=le.content,
            confidence=le.confidence,
            bbox=[schema.make_bbox(_PAGE_ID, le.coord)],
            description=le.description,
        )
        for i, le in enumerate(layout_elements, start=1)
    ]

    return schema.build_document(pages=pages, elements=elements, error_status=[])
