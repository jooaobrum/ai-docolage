"""FastAPI app factory for the Service.

Startup (via the lifespan context manager) does exactly what the spec's
"Running the system" user stories require before the app accepts traffic:

- run pending database migrations (story 5)
- create the object storage bucket if missing (story 6)
- open the Postgres pool and object store client, stashed on app.state so
  routes (health.py today; submit/status/result/stream in later tickets)
  can reach them without a global.

New route groups should be added as their own APIRouter under
api/routes/ and included in `create_app`, matching health.py's shape.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from docolage_service.api.routes import health, jobs, sync_stream
from docolage_service.config import Settings, get_settings
from docolage_service.db.migrator import run_migrations
from docolage_service.db.pool import create_pool
from docolage_service.storage.object_store import ObjectStore, ObjectStoreSettings


def _build_object_store(settings: Settings) -> ObjectStore:
    return ObjectStore(
        ObjectStoreSettings(
            endpoint_url=settings.object_storage_endpoint_url,
            bucket=settings.object_storage_bucket,
            access_key=settings.object_storage_access_key,
            secret_key=settings.object_storage_secret_key,
            region=settings.object_storage_region,
            use_ssl=settings.object_storage_use_ssl,
        )
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await run_migrations(settings.postgres_dsn)

        app.state.pg_pool = await create_pool(
            settings.postgres_dsn,
            min_size=settings.postgres_pool_min_size,
            max_size=settings.postgres_pool_max_size,
        )
        app.state.object_store = _build_object_store(settings)
        await app.state.object_store.ensure_bucket()
        app.state.settings = settings

        yield

        await app.state.pg_pool.close()

    app = FastAPI(title="docolage service", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(sync_stream.router)
    return app


# Default app instance for `uvicorn docolage_service.api.app:app`.
app = create_app()
