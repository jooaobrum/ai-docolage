"""Explicit configuration object for docolage.

No module-level globals and no hardcoded paths anywhere in the package —
every path, threshold, and injectable port flows through this object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class DocolageConfig:
    """Configuration passed explicitly to ``docolage.parse``.

    Attributes:
        model_path: UC Volume path holding the self-hosted VLM weights.
            Only required for documents that reach the VLM path.
        output_volume_path: UC Volume path page PNGs and element crops
            are written to.
        escalation_threshold: Confidence below which a Figure's VLM
            description escalates to the External OpenAI API.
        vlm_recognizer: The VLM recognizer port. Real implementation
            wraps PaddleOCR-VL; tests inject a fake. ``None`` is valid
            for documents that never reach the VLM path (Markdown,
            born-digital PDFs).
        dpi: Rasterization resolution for page PNGs.
    """

    model_path: str = ""
    output_volume_path: str = "."
    escalation_threshold: float = 0.5
    vlm_recognizer: Optional[Any] = None
    dpi: int = 200
    figure_captioner: Optional[Any] = None
    extra: dict = field(default_factory=dict)
