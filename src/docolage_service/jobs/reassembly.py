"""Reassembly of a job's final result from its per-page rows (ticket #22).

Per ADR-0009 ("Reassembly"), pages are ordered by page number and
concatenated with no cross-page reconciliation attempted — the known
quality risk the spec's "Further Notes" section accepts for this MVP.

This module is pure: it takes plain Python data structures (never a DB
connection or pool) so it is unit-testable without Postgres, per spec
Testing Decisions ("no test may reach into the queue module directly").
The route handler (api/routes/jobs.py) is the only caller that touches
the database; it fetches page_results/page_jobs rows, converts them to
the dict shapes documented below, and hands them to `reassemble`.
"""

from __future__ import annotations

from typing import Any, Literal

from docolage import schema

MARKDOWN_FORMAT = "markdown"
ELEMENTS_FORMAT = "elements"

ResultFormat = Literal["markdown", "elements"]


class UnknownFormatError(ValueError):
    """Raised when `reassemble` is asked for a format it doesn't know."""


def reassemble(
    page_results: list[dict[str, Any]],
    page_jobs: list[dict[str, Any]],
    format: ResultFormat,
) -> dict[str, Any]:
    """Build the final document-level result for one job.

    Args:
        page_results: one dict per *successful* page, each shaped
            ``{"page_number": int, "markdown": str, "elements": list[dict]}``
            — ``elements`` already in the Core's ``schema.make_element``
            shape (id/type/content/confidence/description/bbox/image_uri/
            captioned), as persisted by the Worker (worker/main.py).
        page_jobs: one dict per page_job belonging to the job, each shaped
            ``{"page_number": int, "status": str, "error_message": str | None}``.
            Used only to find permanently-failed pages (status == "failed")
            for the ``error_status`` entries (spec user story 57); pages
            in any other status are ignored here — retry/lease semantics
            are ticket #24's concern, not reassembly's.
        format: "markdown" or "elements".

    Returns:
        For format == "markdown": ``{"pages": [{"page_number": int,
        "markdown": str}, ...]}``, ordered by page number, successful
        pages only (spec user stories 45/24 — a markdown-consuming client
        wants a per-page ordered sequence it can concatenate itself, or a
        caller can join ``p["markdown"] for p in result["pages"]``
        directly for a single string).

        For format == "elements": Core's ``schema.build_document`` shape
        — ``{"document": {"pages": [...], "elements": [...]},
        "error_status": [...], "metadata": {...}}`` (ADR-0002) — pages and
        elements ordered by page number, plus one ``error_status`` entry
        per permanently-failed page (spec user stories 51, 52, 57).

    Raises:
        UnknownFormatError: `format` is neither "markdown" nor "elements".
    """
    if format not in (MARKDOWN_FORMAT, ELEMENTS_FORMAT):
        raise UnknownFormatError(f"Unknown result format {format!r}; expected 'markdown' or 'elements'.")

    ordered_results = sorted(page_results, key=lambda p: p["page_number"])
    failed_page_jobs = sorted(
        (pj for pj in page_jobs if pj.get("status") == "failed"),
        key=lambda p: p["page_number"],
    )

    if format == MARKDOWN_FORMAT:
        return {
            "pages": [
                {"page_number": p["page_number"], "markdown": p.get("markdown") or ""}
                for p in ordered_results
            ]
        }

    pages = [
        schema.make_page(page_id=p["page_number"], image_uri="", markdown=p.get("markdown") or "")
        for p in ordered_results
    ]

    elements: list[dict[str, Any]] = []
    next_element_id = 1
    for p in ordered_results:
        for el in p.get("elements") or []:
            element = dict(el)
            element["id"] = next_element_id
            elements.append(element)
            next_element_id += 1

    error_status = [
        {
            "error_message": pj.get("error_message") or "",
            "page_id": pj["page_number"],
        }
        for pj in failed_page_jobs
    ]

    return schema.build_document(pages=pages, elements=elements, error_status=error_status)
