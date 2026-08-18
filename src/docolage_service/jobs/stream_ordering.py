"""Pure ordering logic for the sync SSE stream (ticket #23).

Per ADR-0008, the sync stream is implemented by polling the page-result
table on a short interval and emitting newly-completed pages **in page
order**, tracking a high-water mark per stream (spec user story 25: pages
must be streamed in page order regardless of the order Workers finish
them).

This module holds only the pure "which pages can I emit now" decision so
it can be unit tested with no database, no event loop, and no HTTP layer
involved. The SSE route (api/routes/sync_stream.py) is the only caller.
"""

from __future__ import annotations


def next_contiguous_pages(high_water_mark: int, available_page_numbers: list[int]) -> list[int]:
    """Given the last page number already emitted (`high_water_mark`, 0
    meaning nothing emitted yet) and the set of page numbers currently
    available to emit (e.g. every page_number present in page_results or
    permanently-failed in page_jobs for this job), return the run of page
    numbers that can now be emitted in order.

    Only a *contiguous* run starting at `high_water_mark + 1` is
    returned — a page that finished out of order (e.g. page 3 before page
    2) is withheld until the gap in front of it (page 2) closes, so the
    caller never emits pages out of order and never has to reorder on the
    client side.

    `available_page_numbers` need not be sorted or deduplicated; both are
    handled here.
    """
    available = set(available_page_numbers)
    result: list[int] = []
    next_page = high_water_mark + 1
    while next_page in available:
        result.append(next_page)
        next_page += 1
    return result
