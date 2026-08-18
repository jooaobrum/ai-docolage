"""Pure unit tests for docolage_service.jobs.reassembly (ticket #22).

No DB, no `@pytest.mark.service` — these run in the default suite and are
the primary proof that reassembly logic (page ordering, markdown
concatenation, error_status for partial/failed jobs) is correct, since
Docker/testcontainers are unavailable in this sandbox and the
service-marked HTTP tests can only be structurally verified.
"""

from __future__ import annotations

import pytest

from docolage_service.jobs.reassembly import (
    ELEMENTS_FORMAT,
    MARKDOWN_FORMAT,
    UnknownFormatError,
    reassemble,
)


def _element(el_id: int, page: int) -> dict:
    return {
        "id": el_id,
        "type": "text",
        "content": f"page {page} content",
        "confidence": 0.9,
        "description": "",
        "bbox": [{"page_id": page, "coord": [0, 0, 10, 10]}],
        "image_uri": "",
        "captioned": False,
    }


def _succeeded_page_job(page_number: int) -> dict:
    return {"page_number": page_number, "status": "succeeded", "error_message": None}


def _failed_page_job(page_number: int, error_message: str) -> dict:
    return {"page_number": page_number, "status": "failed", "error_message": error_message}


# --- all pages succeeded -----------------------------------------------


def test_markdown_format_orders_pages_by_number_regardless_of_input_order() -> None:
    page_results = [
        {"page_number": 3, "markdown": "page three", "elements": []},
        {"page_number": 1, "markdown": "page one", "elements": []},
        {"page_number": 2, "markdown": "page two", "elements": []},
    ]
    page_jobs = [_succeeded_page_job(n) for n in (1, 2, 3)]

    result = reassemble(page_results, page_jobs, MARKDOWN_FORMAT)

    assert [p["page_number"] for p in result["pages"]] == [1, 2, 3]
    assert [p["markdown"] for p in result["pages"]] == ["page one", "page two", "page three"]


def test_elements_format_all_succeeded_has_no_error_status_and_all_pages() -> None:
    page_results = [
        {"page_number": 1, "markdown": "md1", "elements": [_element(1, 1)]},
        {"page_number": 2, "markdown": "md2", "elements": [_element(1, 2)]},
    ]
    page_jobs = [_succeeded_page_job(1), _succeeded_page_job(2)]

    result = reassemble(page_results, page_jobs, ELEMENTS_FORMAT)

    assert [p["id"] for p in result["document"]["pages"]] == [1, 2]
    assert [p["markdown"] for p in result["document"]["pages"]] == ["md1", "md2"]
    assert len(result["document"]["elements"]) == 2
    assert result["error_status"] == []
    assert "id" in result["metadata"]
    assert "version" in result["metadata"]


def test_elements_format_reassigns_element_ids_uniquely_across_pages() -> None:
    # Each page's raw elements start their ids at 1 (worker/main.py); after
    # reassembly the document-level element ids must be unique.
    page_results = [
        {"page_number": 1, "markdown": "", "elements": [_element(1, 1), _element(2, 1)]},
        {"page_number": 2, "markdown": "", "elements": [_element(1, 2)]},
    ]
    page_jobs = [_succeeded_page_job(1), _succeeded_page_job(2)]

    result = reassemble(page_results, page_jobs, ELEMENTS_FORMAT)

    ids = [e["id"] for e in result["document"]["elements"]]
    assert ids == [1, 2, 3]
    assert len(set(ids)) == len(ids)


# --- partial: some succeeded, some permanently failed --------------------


def test_partial_job_contains_successful_pages_plus_error_status_per_failed_page() -> None:
    page_results = [
        {"page_number": 1, "markdown": "md1", "elements": [_element(1, 1)]},
        {"page_number": 3, "markdown": "md3", "elements": [_element(1, 3)]},
    ]
    page_jobs = [
        _succeeded_page_job(1),
        _failed_page_job(2, "OCR timed out"),
        _succeeded_page_job(3),
    ]

    result = reassemble(page_results, page_jobs, ELEMENTS_FORMAT)

    assert [p["id"] for p in result["document"]["pages"]] == [1, 3]
    assert len(result["document"]["elements"]) == 2
    assert result["error_status"] == [{"error_message": "OCR timed out", "page_id": 2}]


def test_partial_job_markdown_format_only_includes_successful_pages() -> None:
    page_results = [{"page_number": 1, "markdown": "md1", "elements": []}]
    page_jobs = [_succeeded_page_job(1), _failed_page_job(2, "boom")]

    result = reassemble(page_results, page_jobs, MARKDOWN_FORMAT)

    assert [p["page_number"] for p in result["pages"]] == [1]


def test_multiple_failed_pages_produce_one_error_status_entry_each_in_page_order() -> None:
    page_results = [{"page_number": 2, "markdown": "md2", "elements": []}]
    page_jobs = [
        _failed_page_job(3, "third failed"),
        _succeeded_page_job(2),
        _failed_page_job(1, "first failed"),
    ]

    result = reassemble(page_results, page_jobs, ELEMENTS_FORMAT)

    assert result["error_status"] == [
        {"error_message": "first failed", "page_id": 1},
        {"error_message": "third failed", "page_id": 3},
    ]


# --- all pages failed ----------------------------------------------------


def test_all_pages_failed_has_no_successful_pages_and_one_error_status_per_page() -> None:
    page_results: list[dict] = []
    page_jobs = [_failed_page_job(1, "err1"), _failed_page_job(2, "err2")]

    result = reassemble(page_results, page_jobs, ELEMENTS_FORMAT)

    assert result["document"]["pages"] == []
    assert result["document"]["elements"] == []
    assert result["error_status"] == [
        {"error_message": "err1", "page_id": 1},
        {"error_message": "err2", "page_id": 2},
    ]


def test_all_pages_failed_markdown_format_returns_empty_pages_list() -> None:
    result = reassemble([], [_failed_page_job(1, "err1")], MARKDOWN_FORMAT)
    assert result["pages"] == []


# --- misc ------------------------------------------------------------------


def test_missing_error_message_defaults_to_empty_string() -> None:
    page_jobs = [{"page_number": 1, "status": "failed", "error_message": None}]
    result = reassemble([], page_jobs, ELEMENTS_FORMAT)
    assert result["error_status"] == [{"error_message": "", "page_id": 1}]


def test_non_failed_non_succeeded_page_jobs_are_ignored() -> None:
    # e.g. a page still 'queued' or 'claimed' shouldn't appear anywhere in
    # a reassembled result — reassembly is only ever called for terminal
    # jobs by the route handler, but the function itself should be inert
    # to a page in a non-terminal per-page state rather than erroring.
    page_jobs = [_succeeded_page_job(1), {"page_number": 2, "status": "claimed", "error_message": None}]
    result = reassemble([{"page_number": 1, "markdown": "md1", "elements": []}], page_jobs, ELEMENTS_FORMAT)
    assert result["error_status"] == []
    assert [p["id"] for p in result["document"]["pages"]] == [1]


def test_unknown_format_raises() -> None:
    with pytest.raises(UnknownFormatError):
        reassemble([], [], "xml")  # type: ignore[arg-type]
