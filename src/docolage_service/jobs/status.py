"""Pure job-status derivation from a job's page_job statuses (ticket #24).

Deliberately dependency-free — no asyncpg, no pool, just a list of
strings in and a status string out — so it is unit-testable without
Postgres or testcontainers (spec Testing Decisions: "these should run in
the DEFAULT suite").

Terminal page_job statuses are 'succeeded' and permanently-'failed'
(retries exhausted — see worker/main.py and db/queries.py's
`fail_page_job`, which only sets 'failed' once `page_job_max_attempts`
is reached; short of that it resets the row to 'queued' so it is
reclaimable). 'queued', 'claimed' and 'running' are all still in-flight.

Spec, "Storage and queue": "Job status is derived from its page jobs:
completed when all pages succeeded, partial when at least one succeeded
and at least one permanently failed, failed when none succeeded."
"""

from __future__ import annotations

TERMINAL_PAGE_STATUSES = frozenset({"succeeded", "failed"})

RUNNING = "running"
COMPLETED = "completed"
PARTIAL = "partial"
FAILED = "failed"


def derive_job_status(page_statuses: list[str]) -> str:
    """Derive the parent job's status from its page_jobs' statuses.

    Returns 'running' if there are no page_jobs yet, or if any page_job
    is still non-terminal (queued/claimed/running) — a job must not be
    resolved to a terminal status while any page could still change
    outcome. Once every page_job is terminal (succeeded or permanently
    failed):
      - all succeeded            -> 'completed'
      - some succeeded, some failed -> 'partial'
      - none succeeded (all failed) -> 'failed'
    """
    if not page_statuses:
        return RUNNING
    if any(status not in TERMINAL_PAGE_STATUSES for status in page_statuses):
        return RUNNING

    succeeded = sum(1 for status in page_statuses if status == "succeeded")
    failed = sum(1 for status in page_statuses if status == "failed")

    if failed == 0:
        return COMPLETED
    if succeeded == 0:
        return FAILED
    return PARTIAL
