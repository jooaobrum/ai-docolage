"""Pure unit tests for `next_contiguous_pages` (ticket #23).

Deliberately unmarked (not `@pytest.mark.service`) — no database, no
event loop, no HTTP layer — so it runs in the default suite even though
Docker/testcontainers are unavailable in this sandbox, and is the main
proof of the sync stream's page-ordering correctness (spec user story 25)
until a Docker-backed environment can run test_sync_stream.py.
"""

from __future__ import annotations

from docolage_service.jobs.stream_ordering import next_contiguous_pages


def test_empty_available_yields_nothing() -> None:
    assert next_contiguous_pages(0, []) == []


def test_all_pages_available_at_once_in_order() -> None:
    assert next_contiguous_pages(0, [1, 2, 3]) == [1, 2, 3]


def test_all_pages_available_at_once_shuffled() -> None:
    assert next_contiguous_pages(0, [3, 1, 2]) == [1, 2, 3]


def test_out_of_order_arrival_withholds_until_gap_closes() -> None:
    # Page 3 finished first; page 2 hasn't landed yet, so nothing beyond
    # the (empty) high-water mark can be emitted.
    assert next_contiguous_pages(0, [3]) == []
    # Page 2 lands; still nothing, page 1 hasn't landed.
    assert next_contiguous_pages(0, [2, 3]) == []
    # Page 1 finally lands; all three are now contiguous from 1.
    assert next_contiguous_pages(0, [1, 2, 3]) == [1, 2, 3]


def test_gap_in_the_middle_only_emits_the_leading_run() -> None:
    # High-water mark at 2; page 4 available but page 3 is missing, so
    # only nothing new can be emitted (the run from 3 is broken).
    assert next_contiguous_pages(2, [4]) == []
    # Once page 3 lands too, both 3 and 4 emit.
    assert next_contiguous_pages(2, [3, 4]) == [3, 4]


def test_high_water_mark_advances_and_only_new_pages_are_returned() -> None:
    available = [1, 2, 3]
    first = next_contiguous_pages(0, available)
    assert first == [1, 2, 3]
    hwm = first[-1]
    # Nothing new available beyond the mark -> nothing to emit.
    assert next_contiguous_pages(hwm, available) == []


def test_duplicates_in_available_are_harmless() -> None:
    assert next_contiguous_pages(0, [1, 1, 2, 2, 3]) == [1, 2, 3]


def test_pages_at_or_below_high_water_mark_are_ignored() -> None:
    # Already emitted 1 and 2; even if they reappear in `available`
    # (e.g. a stale poll result), only page 3 onward should emit.
    assert next_contiguous_pages(2, [1, 2, 3]) == [3]


def test_large_contiguous_run_all_at_once() -> None:
    pages = list(range(1, 101))
    assert next_contiguous_pages(0, pages) == pages


def test_no_pages_available_below_high_water_mark_returns_empty() -> None:
    assert next_contiguous_pages(5, []) == []
