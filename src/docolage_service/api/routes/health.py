"""GET /health — reports whether Postgres and object storage are reachable.

Per spec user story 9: "a health endpoint that reports whether Postgres
and object storage are reachable, so that a container orchestrator can
decide whether the API is ready." Non-200 if either dependency is down.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from docolage_service.db.pool import is_reachable as postgres_is_reachable

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    postgres: bool
    object_storage: bool


@router.get("/health", response_model=HealthResponse)
async def health(request: Request, response: Response) -> HealthResponse:
    pool = request.app.state.pg_pool
    object_store = request.app.state.object_store

    postgres_ok = await postgres_is_reachable(pool)
    storage_ok = await object_store.is_reachable()
    ok = postgres_ok and storage_ok

    response.status_code = 200 if ok else 503
    return HealthResponse(
        status="ok" if ok else "degraded",
        postgres=postgres_ok,
        object_storage=storage_ok,
    )
