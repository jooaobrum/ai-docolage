"""A minimal, dependency-free migration runner.

Per spec user story 5 ("database schema migrations run automatically on
API start, so that a fresh checkout works without a manual migration
step"), `run_migrations` is called from the API's startup lifespan before
the app starts serving traffic. Deliberately not Alembic: the schema here
is three tables and a handful of indexes, and a hand-rolled runner keeps
the Service's dependency surface small while still being idempotent,
ordered, and safe to run concurrently from multiple API replicas (guarded
by a Postgres advisory lock).

Migration files live in db/migrations/*.sql, named with a numeric prefix
(0001_init.sql, 0002_*.sql, ...) that determines application order. Each
file is applied at most once, tracked in a `schema_migrations` table.
"""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Arbitrary constant used as a Postgres advisory lock key so concurrent
# API replicas starting at once don't race to apply the same migration.
_MIGRATION_LOCK_KEY = 0x646F636F6C616765  # "docolage" as hex, truncated to fit bigint


async def run_migrations(dsn: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every not-yet-applied migration in `migrations_dir`, in order.

    Returns the list of migration filenames that were newly applied (empty
    if the schema was already current). Safe to call on every API start.
    """
    conn = await asyncpg.connect(dsn=dsn)
    try:
        await conn.execute(f"SELECT pg_advisory_lock({_MIGRATION_LOCK_KEY})")
        try:
            return await _apply_pending(conn, migrations_dir)
        finally:
            await conn.execute(f"SELECT pg_advisory_unlock({_MIGRATION_LOCK_KEY})")
    finally:
        await conn.close()


async def _apply_pending(conn: asyncpg.Connection, migrations_dir: Path) -> list[str]:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename    TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    applied = {row["filename"] for row in await conn.fetch("SELECT filename FROM schema_migrations")}

    newly_applied: list[str] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name in applied:
            continue
        sql = path.read_text()
        logger.info("applying migration %s", path.name)
        async with conn.transaction():
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES ($1)", path.name
            )
        newly_applied.append(path.name)
    return newly_applied
