"""Escalation (CONTEXT.md "Escalation", ADR-0003): the per-element
decision that keeps the External OpenAI API dependency small.

Every Figure is attempted with the VLM first. Only a `type='figure'`
element whose `description` came back empty, or whose `confidence`
falls below `threshold`, escalates to the Figure captioner port. Table,
formula, and chart elements are never eligible, whatever their
confidence — the VLM handles them natively. The decision is made per
element, never per job or per document, and never as a failure
fallback: a confident-but-empty VLM result escalates too.
"""

from __future__ import annotations

import logging
from typing import Optional

from docolage.ports_captioner import FigureCaptioner

logger = logging.getLogger(__name__)

_ESCALATABLE_TYPES = frozenset({"figure"})


def _needs_escalation(element: dict, threshold: float) -> bool:
    if element["type"] not in _ESCALATABLE_TYPES:
        return False
    return element["description"] == "" or element["confidence"] < threshold


def apply_escalation(
    elements: list[dict], figure_captioner: Optional[FigureCaptioner], threshold: float
) -> list[dict]:
    """Return elements with escalated Figures re-described via
    figure_captioner. Non-escalated elements pass through unchanged
    other than an explicit escalated=False. With no captioner configured
    (escalation is optional, CONTEXT.md), elements pass through as-is."""
    result = []
    for element in elements:
        escalate = figure_captioner is not None and _needs_escalation(element, threshold)
        if escalate and figure_captioner is not None:
            try:
                description = figure_captioner.caption(element["image_uri"])
            except Exception:
                # A captioner call that raises (network error, API error,
                # ...) must not fail the page or the document (ticket #9) —
                # keep the VLM's original description and move on. Logged
                # at debug, not surfaced in error_status: this is a
                # degradation of an optional escalation, not a page error.
                logger.debug(
                    "Figure captioner raised for image_uri=%r; keeping the "
                    "original VLM description.",
                    element["image_uri"],
                    exc_info=True,
                )
                element = {**element, "escalated": element.get("escalated", False)}
            else:
                element = {**element, "description": description, "escalated": True}
        else:
            element = {**element, "escalated": element.get("escalated", False)}
        result.append(element)
    return result
