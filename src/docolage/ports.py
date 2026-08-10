"""The VLM recognizer port (spec "The seam").

One injectable seam for the non-hermetic GPU dependency: the real
implementation wraps PaddleOCR-VL's PaddleOCRVL pipeline (a later
slice); FakeVLMRecognizer returns canned output so the Router, cropping,
escalation, and schema assembly are all testable without a GPU.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LayoutElement:
    """One layout element as produced by a VLM recognizer for one page.

    coord is pixel space on that page's rasterized PNG. confidence and
    description follow ai_parse_document's field semantics; description
    is only meaningful for figure elements (escalation, later slice).
    """

    type: str
    content: str
    confidence: float
    coord: list = field(default_factory=list)
    description: str = ""


class VLMRecognizer(Protocol):
    def recognize(self, image_path: str, page_id: int) -> list[LayoutElement]:
        """Return layout elements for one rasterized page, in reading order."""
        ...


class FakeVLMRecognizer:
    """Test double: returns pre-canned elements per page_id, records calls."""

    def __init__(self, elements_by_page: dict[int, list[LayoutElement]]):
        self._elements_by_page = elements_by_page
        self.calls: list[tuple[str, int]] = []

    def recognize(self, image_path: str, page_id: int) -> list[LayoutElement]:
        self.calls.append((image_path, page_id))
        return list(self._elements_by_page.get(page_id, []))
