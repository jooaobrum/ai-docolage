"""Fixtures for Service tests (tests/service/).

Every test here is marked `service` and needs real Postgres + object
storage to be meaningful (spec, "Testing Decisions": "real infrastructure
rather than a fake queue is a deliberate choice"). Two ways to satisfy
that, tried in order:

1. testcontainers-python spins up throwaway Postgres + MinIO containers
   automatically, if a Docker daemon is reachable.
2. Otherwise, point DOCOLAGE_TEST_POSTGRES_DSN / DOCOLAGE_TEST_S3_* at an
   already-running stack (e.g. `docker compose up postgres minio`).

If neither is available, tests collected under this marker are skipped
(never errored) — matching the existing GPU-marked-test honesty pattern.
Run with: `pytest -m service tests/service`.
"""

from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
import pytest_asyncio


def _docker_available() -> bool:
    try:
        import docker  # noqa: F401

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    if os.environ.get("DOCOLAGE_TEST_POSTGRES_DSN"):
        return os.environ["DOCOLAGE_TEST_POSTGRES_DSN"]

    if not _docker_available():
        pytest.skip("no Docker daemon reachable and DOCOLAGE_TEST_POSTGRES_DSN not set")

    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine")
    container.start()
    dsn = container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    yield dsn
    container.stop()


@pytest.fixture(scope="session")
def object_storage_settings() -> dict:
    if os.environ.get("DOCOLAGE_TEST_S3_ENDPOINT"):
        return {
            "endpoint_url": os.environ["DOCOLAGE_TEST_S3_ENDPOINT"],
            "access_key": os.environ.get("DOCOLAGE_TEST_S3_ACCESS_KEY", "minioadmin"),
            "secret_key": os.environ.get("DOCOLAGE_TEST_S3_SECRET_KEY", "minioadmin"),
        }

    if not _docker_available():
        pytest.skip("no Docker daemon reachable and DOCOLAGE_TEST_S3_ENDPOINT not set")

    from testcontainers.minio import MinioContainer

    container = MinioContainer()
    container.start()
    host_ip = container.get_container_host_ip()
    port = container.get_exposed_port(9000)
    settings = {
        "endpoint_url": f"http://{host_ip}:{port}",
        "access_key": "minioadmin",
        "secret_key": "minioadmin",
    }
    yield settings
    container.stop()


@pytest_asyncio.fixture
async def service_app(postgres_dsn: str, object_storage_settings: dict):
    from docolage_service.api.app import create_app
    from docolage_service.config import Settings

    settings = Settings(
        postgres_dsn=postgres_dsn,
        object_storage_endpoint_url=object_storage_settings["endpoint_url"],
        object_storage_bucket="docolage-test",
        object_storage_access_key=object_storage_settings["access_key"],
        object_storage_secret_key=object_storage_settings["secret_key"],
        # ticket #25 set sync_gate_max_pages's production default from a
        # CPU-measured per-page latency (1 page) — too low for these
        # tests' small multi-page fixtures, which exercise sync-path
        # behaviour (ordering, SSE) rather than the gate itself.
        sync_gate_max_pages=50,
    )
    app = create_app(settings=settings)
    async with app.router.lifespan_context(app):
        # Tests share one session-scoped Postgres container (spinning up a
        # fresh container per test is too slow). The claim query is
        # intentionally global — a real worker doesn't know which job to
        # expect — so leftover rows from an earlier test's job would
        # otherwise be claimable by a later test, making results
        # order-dependent. Start every test from an empty queue/job set.
        async with app.state.pg_pool.acquire() as conn:
            await conn.execute("TRUNCATE page_results, page_jobs, jobs CASCADE")
        yield app


@pytest_asyncio.fixture
async def client(service_app) -> AsyncIterator["httpx.AsyncClient"]:  # noqa: F821
    import httpx

    transport = httpx.ASGITransport(app=service_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
