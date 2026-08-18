"""Explicit configuration object for docolage.

No module-level globals and no hardcoded paths anywhere in the package —
every path and injectable port flows through this object. Every tunable
default below is read from docolage.yaml (settings_loader.py) — the one
source of truth shared with the Service's Settings — not hardcoded here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from docolage.office_convert import DEFAULT_CONVERT_TIMEOUT_SECONDS
from docolage.openai_client import DEFAULT_MODEL as _DEFAULT_OPENAI_MODEL
from docolage.settings_loader import section

_recognition = section("recognition")


@dataclass
class DocolageConfig:
    """Configuration passed explicitly to ``docolage.parse``.

    Attributes:
        output_path: Filesystem or object-storage path page PNGs and
            element crops are written to.
        vlm_recognizer: The Recognizer port (docolage.ports).
            ``DoclingRecognizer`` (docolage.recognizer_docling,
            ADR-0010's standard pipeline — layout model + TableFormer +
            OCR-when-needed) is the sole real implementation and the
            default; tests inject a fake. Every PDF/PNG page is now
            recognized through this port (ADR-0010: the Router and the
            born-digital text-layer path are deleted, so there is no
            page that skips it). ``None`` lazily falls back to a
            process-wide ``DoclingRecognizer`` instance (see
            ``default_recognizer`` below) — it remains valid only for
            formats that never reach a page (Markdown).
        dpi: Rasterization resolution for page PNGs.
        office_convert_timeout_seconds: Timeout for the headless
            LibreOffice subprocess that normalizes DOCX/PPTX to PDF
            (docolage.office_convert).
        recognition_confidence_threshold: Below this page-level
            confidence (docling's mean_score, docolage.recognizer_docling),
            a FallbackRecognizer (docolage.recognizer_fallback) escalates
            the page to the OpenAI recognizer, if one is configured.
        openai_model: Model used by both External OpenAI API ports
            (build_openai_captioner, build_openai_recognizer;
            docolage.openai_client) when a caller builds one via this
            config rather than passing ``model=`` explicitly. The
            Service instead reads its own ``Settings.openai_model``
            (docolage_service/config.py is that process's one source of
            truth) and passes it straight to those same builders.
    """

    output_path: str = "."
    vlm_recognizer: Optional[Any] = None
    dpi: int = _recognition["dpi"]
    figure_captioner: Optional[Any] = None
    office_convert_timeout_seconds: int = DEFAULT_CONVERT_TIMEOUT_SECONDS
    recognition_confidence_threshold: float = _recognition["confidence_threshold"]
    openai_model: str = _DEFAULT_OPENAI_MODEL
    extra: dict = field(default_factory=dict)


_default_recognizer_instance: Optional[Any] = None


def default_recognizer() -> Any:
    """The process-wide default Recognizer: docling's standard pipeline
    (ADR-0010), lazily constructed and reused on first use.

    Lazy and memoized so that constructing a ``DocolageConfig`` (which
    tests and non-PDF formats do freely, without ever wanting a GPU or a
    model download) stays free; the cost of loading docling's models is
    paid once, on the first page that actually needs recognizing, not on
    every ``DocolageConfig()``.
    """
    global _default_recognizer_instance
    if _default_recognizer_instance is None:
        from docolage.recognizer_docling import DoclingRecognizer

        _default_recognizer_instance = DoclingRecognizer()
    return _default_recognizer_instance
