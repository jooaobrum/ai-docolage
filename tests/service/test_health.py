"""HTTP-observable behaviour of GET /health.

Per spec user story 9 and Testing Decisions "Seam 2 — the Service": drives
the API through an in-process ASGI client against real throwaway
Postgres + object storage, asserting only on what an HTTP client sees.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.service


@pytest.mark.asyncio
async def test_health_reports_ok_when_dependencies_reachable(client) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "postgres": True, "object_storage": True}


@pytest.mark.asyncio
async def test_health_reports_degraded_when_postgres_unreachable(service_app) -> None:
    import httpx

    # Sabotage the pool's reachability without tearing down the app: close
    # it out from under the running app, which is what an outage looks
    # like to /health.
    await service_app.state.pg_pool.close()

    transport = httpx.ASGITransport(app=service_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["postgres"] is False
