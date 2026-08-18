-- Add 'cancelled' as a terminal status for jobs and page_jobs, so a job
-- can be stopped without waiting for it to fail or complete on its own.
--
-- Postgres names an inline CHECK constraint <table>_<column>_check by
-- default (confirmed against 0001_init.sql's unnamed CHECKs), so those are
-- the constraint names being replaced here.

ALTER TABLE jobs DROP CONSTRAINT jobs_status_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_status_check
    CHECK (status IN ('queued', 'running', 'completed', 'partial', 'failed', 'cancelled'));

ALTER TABLE page_jobs DROP CONSTRAINT page_jobs_status_check;
ALTER TABLE page_jobs ADD CONSTRAINT page_jobs_status_check
    CHECK (status IN ('queued', 'claimed', 'running', 'succeeded', 'failed', 'cancelled'));
