"""asyncpg connection pool lifecycle helpers.

Kept separate from db/migrator.py (which uses its own short-lived
connection, deliberately not the pool, so it can run before the app
declares itself ready) and from api/app.py (which owns *when* the pool is
created and closed, via the FastAPI lifespan).
"""

from __future__ import annotations

import asyncpg

from docolage.settings_loader import section

_postgres = section("postgres")


async def create_pool(
    dsn: str,
    min_size: int = _postgres["pool_min_size"],
    max_size: int = _postgres["pool_max_size"],
) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)


async def is_reachable(pool: asyncpg.Pool) -> bool:
    """Best-effort liveness check used by /health. Never raises."""
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 - health check must not raise
        return False
