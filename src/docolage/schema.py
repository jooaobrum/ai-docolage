"""Assembly of the ai_parse_document-shaped output dict (ADR-0002).

document.elements sits at the document level, not nested inside pages.
Every bbox entry carries its own page_id. This module owns only shape
assembly — callers build page and element dicts, this module wires them
into the final document.
"""

from __future__ import annotations

import uuid

PACKAGE_VERSION = "0.1.0"


def make_bbox(page_id: int, coord: list) -> dict:
    return {"coord": coord, "page_id": page_id}


def make_element(
    element_id: int,
    element_type: str,
    content: str,
    confidence: float,
    bbox: list,
    description: str = "",
    image_uri: str = "",
    escalated: bool = False,
) -> dict:
    """image_uri is only populated for Table and Figure elements (their
    standalone crop file). Text and formula elements leave it empty —
    they're represented via content + bbox into the page PNG only
    (CONTEXT.md "Element crop"). escalated records whether description
    came from the Figure captioner port rather than the VLM, so External
    OpenAI API usage can be audited and cost-attributed (CONTEXT.md
    "Escalation")."""
    return {
        "id": element_id,
        "type": element_type,
        "content": content,
        "confidence": confidence,
        "description": description,
        "bbox": bbox,
        "image_uri": image_uri,
        "escalated": escalated,
    }


def make_page(page_id: int, image_uri: str) -> dict:
    return {"id": page_id, "image_uri": image_uri}


def build_document(pages: list, elements: list, error_status: list) -> dict:
    return {
        "document": {
            "pages": pages,
            "elements": elements,
        },
        "error_status": error_status,
        "metadata": {
            "id": str(uuid.uuid4()),
            "version": PACKAGE_VERSION,
        },
    }
