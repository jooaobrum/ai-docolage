"""Figure captioning (CONTEXT.md "Figure captioner", ADR-0010): the
per-element decision that keeps the External OpenAI API dependency
optional.

ADR-0010 removed docling's per-figure confidence, so there is nothing
left to threshold on. The rule is now unconditional: every
``type="figure"`` element gets captioned when a Figure captioner is
configured, and carries no description when one is not. Table,
formula, and chart elements are never eligible for captioning — the
Recognizer handles them natively.
"""

from __future__ import annotations

import logging
from typing import Optional

from docolage.ports_captioner import FigureCaptioner

logger = logging.getLogger(__name__)

_CAPTIONABLE_TYPES = frozenset({"figure"})


def caption_figures(elements: list[dict], figure_captioner: Optional[FigureCaptioner]) -> list[dict]:
    """Return elements with every Figure described via figure_captioner.

    With no captioner configured, elements pass through unchanged —
    captioning is optional end to end (spec user story 49). A captioner
    call that raises for one figure must not fail the page or the
    document (spec user story 50): that figure simply keeps its
    (empty) description and processing continues.
    """
    if figure_captioner is None:
        return elements

    result = []
    for element in elements:
        if element["type"] not in _CAPTIONABLE_TYPES:
            result.append(element)
            continue
        try:
            description = figure_captioner.caption(element["image_uri"])
        except Exception:
            # Logged at debug, not surfaced in error_status: this is a
            # degradation of an optional dependency, not a page error.
            logger.debug(
                "Figure captioner raised for image_uri=%r; leaving the "
                "figure without a description.",
                element["image_uri"],
                exc_info=True,
            )
            result.append(element)
        else:
            result.append({**element, "description": description, "captioned": True})
    return result
