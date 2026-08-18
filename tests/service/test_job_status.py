"""Pure unit tests for `docolage_service.jobs.status.derive_job_status`
(ticket #24).

Deliberately DB-free — no Postgres, no testcontainers, no `service`
marker — so these run in the *default* suite (spec Testing Decisions:
"the default test run must stay green on a laptop with nothing
running"). They are the main proof of the status-derivation correctness
given Docker/testcontainers are unavailable in this sandbox; the
Postgres-backed retry/lease-recovery behaviour is covered separately in
tests/service/test_retry_and_lease.py, gated behind the `service` marker.
"""

from __future__ import annotations

from docolage_service.jobs.status import derive_job_status


def test_all_succeeded_is_completed() -> None:
    assert derive_job_status(["succeeded", "succeeded", "succeeded"]) == "completed"


def test_one_succeeded_is_completed() -> None:
    assert derive_job_status(["succeeded"]) == "completed"


def test_some_succeeded_some_failed_is_partial() -> None:
    assert derive_job_status(["succeeded", "failed", "succeeded"]) == "partial"


def test_all_failed_is_failed() -> None:
    assert derive_job_status(["failed", "failed"]) == "failed"


def test_no_pages_is_running() -> None:
    # Not reachable in practice (a job always has >=1 page_job), but the
    # function must not crash or claim a terminal status for an empty set.
    assert derive_job_status([]) == "running"


def test_any_queued_page_keeps_job_running_even_with_terminal_pages() -> None:
    assert derive_job_status(["succeeded", "failed", "queued"]) == "running"


def test_any_claimed_page_keeps_job_running() -> None:
    assert derive_job_status(["succeeded", "claimed"]) == "running"


def test_all_still_running_state_pages_keeps_job_running() -> None:
    assert derive_job_status(["queued", "queued", "claimed"]) == "running"


def test_mix_of_non_terminal_states_keeps_job_running() -> None:
    assert derive_job_status(["queued", "running", "succeeded"]) == "running"
