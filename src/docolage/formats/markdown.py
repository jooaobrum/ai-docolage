"""Markdown format handler: text -> ai_parse_document-shaped dict.

Markdown has no rasterized pages (spec: bypasses rasterization, the
Router, and the GPU entirely), so document.pages is empty and every
element's bbox uses a placeholder page_id of 1 with no real geometry
— there is no page image for a coordinate to be relative to.
"""

from __future__ import annotations

from pathlib import Path

from docolage import schema
from docolage.config import DocolageConfig
from docolage.markdown_parser import parse_markdown

_MARKDOWN_PAGE_ID = 1


def parse(path: Path, config: DocolageConfig) -> dict:
    text = path.read_text(encoding="utf-8")
    blocks = parse_markdown(text)

    elements = []
    for element_id, block in enumerate(blocks, start=1):
        bbox = [schema.make_bbox(page_id=_MARKDOWN_PAGE_ID, coord=[])]
        elements.append(
            schema.make_element(
                element_id=element_id,
                element_type=block["type"],
                content=block["content"],
                confidence=1.0,
                bbox=bbox,
            )
        )

    return schema.build_document(pages=[], elements=elements, error_status=[])
