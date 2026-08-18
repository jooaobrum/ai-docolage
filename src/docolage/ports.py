"""The VLM recognizer port (spec "The seam").

One injectable seam for the non-hermetic model dependency: the real
implementation wraps docling's standard pipeline (DoclingRecognizer,
ADR-0010, the sole recognizer); FakeVLMRecognizer returns canned
output so recognition, cropping, escalation, and schema assembly are
all testable without loading real models.
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


@dataclass
class RecognitionResult:
    """Everything one ``recognize()`` call produces for one page.

    ``elements`` is the structured layout element list (unchanged
    contract). ``markdown`` is that same page rendered as markdown
    (spec user stories 45/46, ticket #16) — a second, independent view
    of the page, not derived from ``elements`` by callers. Later,
    ticket #21's worker loop reads ``markdown`` directly off this
    result to build the per-page SSE/result payload.
    """

    elements: list[LayoutElement]
    markdown: str = ""


class VLMRecognizer(Protocol):
    def recognize(self, image_path: str, page_id: int) -> RecognitionResult:
        """Return layout elements (in reading order) and rendered markdown
        for one rasterized page."""
        ...


class FakeVLMRecognizer:
    """Test double: returns pre-canned elements (and optionally markdown)
    per page_id, records calls.

    failing_pages maps page_id to the exception to raise instead of
    returning a result, so tests can drive a page-level failure (render
    failure, OOM, timeout) through the same seam used by the real
    recognizer, for ADR-0006 per-page isolation.

    markdown_by_page is optional per-page canned markdown; pages not
    present there get a simple synthesized markdown (elements' content
    joined by blank lines) so existing tests that don't care about
    markdown keep working unchanged.
    """

    def __init__(
        self,
        elements_by_page: dict[int, list[LayoutElement]],
        failing_pages: "dict[int, Exception] | None" = None,
        markdown_by_page: "dict[int, str] | None" = None,
    ):
        self._elements_by_page = elements_by_page
        self._failing_pages = failing_pages or {}
        self._markdown_by_page = markdown_by_page or {}
        self.calls: list[tuple[str, int]] = []

    def recognize(self, image_path: str, page_id: int) -> RecognitionResult:
        self.calls.append((image_path, page_id))
        if page_id in self._failing_pages:
            raise self._failing_pages[page_id]
        elements = list(self._elements_by_page.get(page_id, []))
        if page_id in self._markdown_by_page:
            markdown = self._markdown_by_page[page_id]
        else:
            markdown = "\n\n".join(el.content for el in elements if el.content)
        return RecognitionResult(elements=elements, markdown=markdown)
