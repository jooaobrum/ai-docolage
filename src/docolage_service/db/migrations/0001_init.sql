-- Initial schema: jobs, page_jobs, page_results.
--
-- See docs/spec/0002-docolage-service-off-databricks.md, "Storage and
-- queue": Postgres holds job metadata, the page-job queue, and completed
-- page results.

CREATE TABLE IF NOT EXISTS jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_mode   TEXT NOT NULL CHECK (delivery_mode IN ('sync', 'async')),
    priority        INTEGER NOT NULL DEFAULT 0,
    source_uri      TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    format          TEXT NOT NULL,
    total_pages     INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'running', 'completed', 'partial', 'failed')),
    error_summary   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS page_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    page_number     INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'claimed', 'running', 'succeeded', 'failed')),
    -- Denormalized from jobs.priority (ADR-0008: "page jobs carry
    -- priority") so the claim query needs no join against jobs.
    priority        INTEGER NOT NULL DEFAULT 0,
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    claimed_at      TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_id, page_number)
);

-- Workers claim with `ORDER BY priority DESC, created_at ASC ... FOR UPDATE
-- SKIP LOCKED`; this index keeps that scan cheap.
CREATE INDEX IF NOT EXISTS idx_page_jobs_claim
    ON page_jobs (status, priority DESC, created_at)
    WHERE status = 'queued';

-- Lease expiry sweep: "claimed rows whose lease has passed" becomes
-- claimable again (spec user story 54).
CREATE INDEX IF NOT EXISTS idx_page_jobs_lease
    ON page_jobs (lease_expires_at)
    WHERE status = 'claimed';

CREATE TABLE IF NOT EXISTS page_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    page_number     INTEGER NOT NULL,
    markdown        TEXT,
    elements        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_id, page_number)
);

-- Sync stream polling reads "new page results for job X since high-water
-- mark", in page order.
CREATE INDEX IF NOT EXISTS idx_page_results_job_page
    ON page_results (job_id, page_number);
