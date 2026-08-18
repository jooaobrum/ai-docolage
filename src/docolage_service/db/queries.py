"""SQL for job submission and status, run against the asyncpg pool.

Kept separate from the route handlers (api/routes/jobs.py) so the SQL
surface — the thing later tickets (worker loop, result retrieval, sync
stream) will also touch — has one place to live, per db/pool.py's existing
separation of "when the pool is created" from "what runs on it".

Priority convention (see db/migrations/0001_init.sql comment and
ADR-0008): higher integer = claimed first. Workers claim with
`ORDER BY priority DESC, created_at ASC`. Sync submissions get a higher
priority value than async ones so a large async document cannot make a
waiting sync client queue behind it (spec user story 40).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import asyncpg

from docolage.settings_loader import section
from docolage_service.jobs.status import derive_job_status

# Sync jobs are claimed ahead of async jobs; see module docstring.
# Sourced from docolage.yaml's "queue" section, the one source of truth.
_queue = section("queue")
SYNC_PRIORITY: int = _queue["sync_priority"]
ASYNC_PRIORITY: int = _queue["async_priority"]

@dataclass
class JobRow:
    id: str
    delivery_mode: str
    priority: int
    source_uri: str
    original_filename: str
    format: str
    total_pages: int
    status: str
    error_summary: str | None
    created_at: object
    updated_at: object

async def insert_job_with_pages(
    pool: asyncpg.Pool,
    *,
    delivery_mode: str,
    priority: int,
    source_uri: str,
    original_filename: str,
    format: str,
    total_pages: int,
) -> str:
    """Insert one job row plus one page_jobs row per page, in a single
    transaction (spec: submit must do this atomically). Returns the new
    job id."""
    async with pool.acquire() as conn, conn.transaction():
        job_id = await conn.fetchval(
            """
            INSERT INTO jobs (delivery_mode, priority, source_uri, original_filename, format, total_pages)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            delivery_mode,
            priority,
            source_uri,
            original_filename,
            format,
            total_pages,
        )
        await conn.executemany(
            """
            INSERT INTO page_jobs (job_id, page_number, priority)
            VALUES ($1, $2, $3)
            """,
            [(job_id, page_number, priority) for page_number in range(1, total_pages + 1)],
        )
        return str(job_id)

async def fetch_job(pool: asyncpg.Pool, job_id: str) -> JobRow | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, delivery_mode, priority, source_uri, original_filename,
                   format, total_pages, status, error_summary, created_at, updated_at
            FROM jobs
            WHERE id = $1::uuid
            """,
            job_id,
        )
    if row is None:
        return None
    return JobRow(**dict(row))

async def count_pages_done(pool: asyncpg.Pool, job_id: str) -> int:
    """Pages in a terminal-success state for this job (spec: "pages done
    out of total")."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM page_jobs WHERE job_id = $1::uuid AND status = 'succeeded'",
            job_id,
        )


async def count_queued_page_jobs(pool: asyncpg.Pool) -> int:
    """Page jobs waiting to be claimed -- sampled periodically for the
    docolage_queue_depth metrics gauge."""
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT count(*) FROM page_jobs WHERE status = 'queued'")


@dataclass
class ClaimedPageJob:
    """One page_job claimed by a Worker, joined with the parent job's
    source document details it needs to rasterize and parse the page."""

    id: str
    job_id: str
    page_number: int
    priority: int
    attempt_count: int
    source_uri: str
    format: str
    original_filename: str


# The atomic claim statement (spec, "Storage and queue"): "Workers claim
# work with a single atomic statement that selects the highest-priority
# oldest unclaimed page job, skipping rows locked by other Workers, and
# marks it claimed with a lease expiry in the same transaction."
#
# The inner SELECT ... FOR UPDATE SKIP LOCKED picks one eligible row,
# ordered by priority DESC, created_at ASC (matches idx_page_jobs_claim /
# idx_page_jobs_lease); SKIP LOCKED means a concurrent Worker running the
# same statement never blocks on, or re-selects, a row another Worker
# already has locked. The outer UPDATE ... RETURNING claims that exact
# row (status, claimed_at, lease_expires_at, attempt_count) in the same
# statement/transaction, so there is no window between "select a
# candidate" and "mark it claimed" for two Workers to both grab it.
#
# Eligible rows (ticket #24, spec user story 54 / "Storage and queue"):
# either freshly 'queued', or 'claimed' with an expired lease — the
# latter is what makes a crashed Worker's page reclaimable. Either way
# attempt_count is incremented and the lease reset exactly like a fresh
# claim, so retry-limit accounting stays correct even for crash-based
# failures (spec: "Attempt count is incremented on claim so that retries
# are bounded even when the failure is a crash rather than an
# exception"). The parent job's updated_at is also touched here so the
# stall-timeout check (story 60) sees this as progress.
_CLAIM_SQL = """
WITH claimed AS (
    UPDATE page_jobs
    SET status = 'claimed',
        claimed_at = now(),
        lease_expires_at = now() + make_interval(secs => $1),
        attempt_count = attempt_count + 1,
        updated_at = now()
    WHERE id = (
        SELECT id
        FROM page_jobs
        WHERE status = 'queued'
           OR (status = 'claimed' AND lease_expires_at < now())
        ORDER BY priority DESC, created_at ASC
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id, job_id, page_number, priority, attempt_count
),
touched_job AS (
    UPDATE jobs SET updated_at = now()
    WHERE id = (SELECT job_id FROM claimed)
)
SELECT
    claimed.id,
    claimed.job_id,
    claimed.page_number,
    claimed.priority,
    claimed.attempt_count,
    jobs.source_uri,
    jobs.format,
    jobs.original_filename
FROM claimed
JOIN jobs ON jobs.id = claimed.job_id
"""


async def claim_next_page_job(pool: asyncpg.Pool, lease_seconds: int) -> ClaimedPageJob | None:
    """Claim and return the single highest-priority oldest queued page
    job, or None if nothing is claimable right now."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(_CLAIM_SQL, lease_seconds)
    if row is None:
        return None
    return ClaimedPageJob(**dict(row))


async def mark_job_running_if_needed(pool: asyncpg.Pool, job_id: str) -> None:
    """First claim on a job flips it from 'queued' to 'running'."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET status = 'running', updated_at = now() "
            "WHERE id = $1::uuid AND status = 'queued'",
            job_id,
        )


async def complete_page_job(
    pool: asyncpg.Pool,
    *,
    page_job_id: str,
    job_id: str,
    page_number: int,
    markdown: str,
    elements: str,
) -> None:
    """Persist the page result and mark the page_job succeeded, in one
    transaction, then finalize the parent job if every page is now done."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO page_results (job_id, page_number, markdown, elements)
            VALUES ($1::uuid, $2, $3, $4::jsonb)
            ON CONFLICT (job_id, page_number) DO UPDATE
                SET markdown = EXCLUDED.markdown, elements = EXCLUDED.elements
            """,
            job_id,
            page_number,
            markdown,
            elements,
        )
        await conn.execute(
            "UPDATE page_jobs SET status = 'succeeded', updated_at = now() WHERE id = $1::uuid",
            page_job_id,
        )
        await conn.execute(
            "UPDATE jobs SET updated_at = now() WHERE id = $1::uuid", job_id
        )
        await _finalize_job_if_terminal(conn, job_id)


async def fail_page_job(
    pool: asyncpg.Pool,
    *,
    page_job_id: str,
    job_id: str,
    error_message: str,
    max_attempts: int,
) -> None:
    """Record a page_job failure (spec user story 53/55, ticket #24).

    If the page_job's `attempt_count` (already incremented at claim time)
    has not yet reached `max_attempts`, the row is reset to 'queued' so
    the claim query (`_CLAIM_SQL`) picks it up again like any other
    queued page — this is the retry path. Only once attempts are
    exhausted is the row marked permanently 'failed'. Either way the
    latest error message is recorded for diagnosability (story 55).

    `status = 'failed'` therefore always means *permanently* failed
    throughout the rest of this module (reassembly.py and the job-result
    route rely on that: they treat page_jobs.status == 'failed' as
    terminal and error-worthy, never as "will be retried").
    """
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT attempt_count FROM page_jobs WHERE id = $1::uuid FOR UPDATE",
            page_job_id,
        )
        attempt_count = row["attempt_count"] if row is not None else max_attempts
        exhausted = attempt_count >= max_attempts
        new_status = "failed" if exhausted else "queued"

        await conn.execute(
            """
            UPDATE page_jobs
            SET status = $2,
                error_message = $3,
                claimed_at = NULL,
                lease_expires_at = NULL,
                updated_at = now()
            WHERE id = $1::uuid
            """,
            page_job_id,
            new_status,
            error_message,
        )
        await conn.execute(
            "UPDATE jobs SET updated_at = now() WHERE id = $1::uuid", job_id
        )
        await _finalize_job_if_terminal(conn, job_id)


async def fail_stalled_jobs(pool: asyncpg.Pool, stall_timeout_seconds: float) -> int:
    """Mark any 'running' job whose `updated_at` has not moved within
    `stall_timeout_seconds` as 'failed' (spec user story 60: "a job is
    marked failed if it has made no progress for a configured period").

    `updated_at` is bumped on every claim (including lease-reclaims) and
    every page completion/failure for the job (see `_CLAIM_SQL`,
    `complete_page_job`, `fail_page_job`), so "no progress" here means
    literally that — no page_job belonging to this job has moved in that
    window. Returns the number of jobs marked failed, for logging."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE jobs
            SET status = 'failed', updated_at = now()
            WHERE status = 'running'
              AND updated_at < now() - make_interval(secs => $1)
            """,
            stall_timeout_seconds,
        )
    # asyncpg command tags look like "UPDATE 3"; fall back to 0 if unparsable.
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):  # pragma: no cover - defensive
        return 0


async def fetch_page_results(pool: asyncpg.Pool, job_id: str) -> list[dict]:
    """All succeeded page_results for a job, ordered by page number
    (ticket #22 reassembly reads this and hands it straight to
    `jobs.reassembly.reassemble` after decoding `elements` from JSON)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT page_number, markdown, elements
            FROM page_results
            WHERE job_id = $1::uuid
            ORDER BY page_number
            """,
            job_id,
        )
    return [
        {
            "page_number": row["page_number"],
            "markdown": row["markdown"],
            "elements": json.loads(row["elements"]) if row["elements"] is not None else [],
        }
        for row in rows
    ]


async def fetch_page_jobs(pool: asyncpg.Pool, job_id: str) -> list[dict]:
    """All page_jobs for a job (ticket #22 reassembly uses this to find
    permanently-failed pages for `error_status` entries)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT page_number, status, error_message
            FROM page_jobs
            WHERE job_id = $1::uuid
            ORDER BY page_number
            """,
            job_id,
        )
    return [dict(row) for row in rows]


@dataclass
class PageOutcome:
    """One page's terminal outcome as seen by the sync stream poller —
    either a completed result (markdown present) or a permanent page_job
    failure (error present), never both."""

    page_number: int
    markdown: str | None
    error: str | None


async def fetch_page_outcomes_since(
    pool: asyncpg.Pool, job_id: str, since_page_number: int
) -> list[PageOutcome]:
    """Every page beyond `since_page_number` that has reached a terminal
    state for this job — succeeded (has a page_results row) or
    permanently failed (page_jobs.status = 'failed') — used by the sync
    stream (ticket #23, ADR-0008) to poll for newly-completed pages.
    Order is not guaranteed here; the caller (stream_ordering) is
    responsible for only emitting a contiguous run in page order.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT page_number, markdown, NULL AS error
            FROM page_results
            WHERE job_id = $1::uuid AND page_number > $2
            UNION ALL
            SELECT page_number, NULL AS markdown, error_message AS error
            FROM page_jobs
            WHERE job_id = $1::uuid AND page_number > $2 AND status = 'failed'
            """,
            job_id,
            since_page_number,
        )
    return [PageOutcome(page_number=r["page_number"], markdown=r["markdown"], error=r["error"]) for r in rows]


async def cancel_job(pool: asyncpg.Pool, job_id: str) -> str | None:
    """Cancel a job: mark it and any not-yet-claimed/claimed page_jobs
    'cancelled'. A no-op (returns the unchanged status) if the job is
    already in a terminal state — cancelling a finished job isn't an
    error, just nothing to do. Returns None if the job doesn't exist.

    A page_job already claimed by a Worker still gets flipped to
    'cancelled' here, but the Worker processing it has no way to learn
    that mid-flight; when it later calls `complete_page_job` or
    `fail_page_job`, those act on the specific page_job_id regardless of
    its current status, so the row's terminal state ends up whichever
    wrote last. `_finalize_job_if_terminal`'s guard excludes 'cancelled'
    from further overwrites, so the parent job's 'cancelled' status is
    not clobbered even if such a page later reports success.
    """
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """
            UPDATE jobs SET status = 'cancelled', updated_at = now()
            WHERE id = $1::uuid AND status NOT IN ('completed', 'partial', 'failed', 'cancelled')
            RETURNING status
            """,
            job_id,
        )
        if row is None:
            existing = await conn.fetchrow("SELECT status FROM jobs WHERE id = $1::uuid", job_id)
            return existing["status"] if existing is not None else None

        await conn.execute(
            """
            UPDATE page_jobs SET status = 'cancelled', updated_at = now()
            WHERE job_id = $1::uuid AND status IN ('queued', 'claimed')
            """,
            job_id,
        )
        return row["status"]


async def retry_job(pool: asyncpg.Pool, job_id: str) -> str | None:
    """Requeue a 'failed' or 'partial' job: reset its permanently-failed
    page_jobs back to 'queued' with a fresh attempt budget, and flip the
    job itself back to 'queued' so the existing claim query and
    `mark_job_running_if_needed` pick it up exactly like a new submission.

    Returns the job's new status ('queued'), or None if the job doesn't
    exist, or the unchanged current status if it isn't in a retryable
    state (caller turns that into a 409).
    """
    async with pool.acquire() as conn, conn.transaction():
        job = await conn.fetchrow("SELECT status FROM jobs WHERE id = $1::uuid FOR UPDATE", job_id)
        if job is None:
            return None
        if job["status"] not in ("failed", "partial"):
            return job["status"]

        await conn.execute(
            """
            UPDATE page_jobs
            SET status = 'queued',
                error_message = NULL,
                claimed_at = NULL,
                lease_expires_at = NULL,
                attempt_count = 0,
                updated_at = now()
            WHERE job_id = $1::uuid AND status = 'failed'
            """,
            job_id,
        )
        row = await conn.fetchrow(
            """
            UPDATE jobs SET status = 'queued', error_summary = NULL, updated_at = now()
            WHERE id = $1::uuid
            RETURNING status
            """,
            job_id,
        )
        return row["status"]


async def list_jobs(
    pool: asyncpg.Pool,
    *,
    status: str | None = None,
    stale_only: bool = False,
    stale_after_seconds: float | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[JobRow]:
    """List jobs, newest first, optionally filtered by status and/or
    restricted to 'running' jobs that have made no progress for
    `stale_after_seconds` (defaults to the same `job_stall_timeout_seconds`
    `fail_stalled_jobs` uses) — a read-only view of the same staleness
    check, for spotting stuck jobs without mutating them.
    """
    conditions: list[str] = []
    params: list[object] = []

    if status is not None:
        params.append(status)
        conditions.append(f"status = ${len(params)}")

    if stale_only:
        seconds = stale_after_seconds if stale_after_seconds is not None else _queue["job_stall_timeout_seconds"]
        params.append(seconds)
        conditions.append(f"status = 'running' AND updated_at < now() - make_interval(secs => ${len(params)})")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, delivery_mode, priority, source_uri, original_filename,
                   format, total_pages, status, error_summary, created_at, updated_at
            FROM jobs
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
    return [JobRow(**dict(row)) for row in rows]


async def _finalize_job_if_terminal(conn: asyncpg.Connection, job_id: str) -> None:
    """If every page_job for job_id has reached a terminal state
    (succeeded, or permanently failed — never queued/claimed/running,
    which are still in-flight or retryable), derive and set the parent
    job's terminal status via `derive_job_status` (ticket #24):
    completed / partial / failed. Otherwise leaves the job's status
    alone — it stays 'running' until every page resolves."""
    rows = await conn.fetch(
        "SELECT status FROM page_jobs WHERE job_id = $1::uuid",
        job_id,
    )
    derived = derive_job_status([row["status"] for row in rows])
    if derived == "running":
        return
    await conn.execute(
        """
        UPDATE jobs SET status = $2, updated_at = now()
        WHERE id = $1::uuid AND status NOT IN ('completed', 'partial', 'failed', 'cancelled')
        """,
        job_id,
        derived,
    )
