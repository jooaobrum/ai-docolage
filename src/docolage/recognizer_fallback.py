"""Page-level recognition fallback (ADR-0010: "a cheap pre-filter can be
reintroduced later behind the [Recognizer] port").

Docling's standard pipeline exposes a page-level confidence
(``_page_confidence`` in recognizer_docling.py, shared across every element
on that page) but no finer signal. ``FallbackRecognizer`` composes the
primary (docling) recognizer with the OpenAI recognizer
(recognizer_openai.py) behind that one signal: below a configured
threshold, re-recognize the page with the stronger model instead.

With no OpenAI recognizer configured (no ``OPENAI_API_KEY``), the fallback
never runs — the primary's result is always returned unchanged, matching
Figure captioning's degrade-to-nothing posture (ADR-0010).
"""

from __future__ import annotations

import logging
from typing import Optional

from docolage.ports import RecognitionResult, VLMRecognizer

logger = logging.getLogger(__name__)


class FallbackRecognizer:
    """Real ``VLMRecognizer`` that escalates a low-confidence page from
    ``primary`` to ``fallback``.

    ``fallback`` may be None (no OPENAI_API_KEY configured), in which case
    this always returns ``primary``'s result untouched. A ``fallback`` call
    that raises must not fail the page (same reasoning as figure
    captioning: an optional dependency degrading, not a page error,
    ADR-0006 per-page isolation) — the primary's (low-confidence) result is
    returned instead.
    """

    def __init__(
        self,
        primary: VLMRecognizer,
        fallback: Optional[VLMRecognizer],
        confidence_threshold: float,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._confidence_threshold = confidence_threshold

    def recognize(self, image_path: str, page_id: int) -> RecognitionResult:
        result = self._primary.recognize(image_path, page_id)
        if self._fallback is None:
            return result

        confidence = result.elements[0].confidence if result.elements else 1.0
        if confidence >= self._confidence_threshold:
            return result

        try:
            return self._fallback.recognize(image_path, page_id)
        except Exception:
            logger.debug(
                "Recognition fallback raised for image_path=%r page_id=%s; "
                "keeping the low-confidence primary result.",
                image_path,
                page_id,
                exc_info=True,
            )
            return result
