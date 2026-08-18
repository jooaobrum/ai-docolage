"""The Figure captioner port (CONTEXT.md "VLM recognizer / Figure
captioner", ADR-0003).

One injectable seam for the non-hermetic External OpenAI API dependency.
The real implementation (ticket #9) wraps the `openai` client; the fake
here returns canned descriptions and records which calls it received, so
figure captioning (ADR-0010: unconditional when a key is configured) is
fully testable without a network.
"""

from __future__ import annotations

from typing import Optional, Protocol


class FigureCaptioner(Protocol):
    def caption(self, image_path: str) -> str:
        """Return a description for the crop at image_path."""
        ...


class FakeFigureCaptioner:
    """Test double: returns a pre-canned description per image_path,
    falling back to `default` when the path isn't in `descriptions`.
    Records every image_path it was asked to caption, in call order, so
    tests can assert exactly which elements were captioned."""

    def __init__(self, descriptions: Optional[dict[str, str]] = None, default: str = "a captioned figure"):
        self._descriptions = descriptions or {}
        self._default = default
        self.calls: list[str] = []

    def caption(self, image_path: str) -> str:
        self.calls.append(image_path)
        return self._descriptions.get(image_path, self._default)
