import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from bank_project.application import ApplicationServices
from bank_project.main import create_app
from bank_project.pipeline.service import Pipeline
from bank_project.query.service import QueryService
from bank_project.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client():
    app = create_app(Settings(graph_backend="none", _env_file=None))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client


async def test_framework_starts_without_database(client):
    assert (await client.get("/health")).json()["mode"] == "framework"
    assert (await client.get("/ready")).json()["graph_backend"] == "none"
    assert (await client.get("/docs")).status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "POST",
            "/api/v1/imports",
            {
                "dataset_id": "test",
                "batch_id": "batch",
                "source_system": "test",
                "source_uri": "reserved://source",
            },
        ),
        (
            "POST",
            "/api/v1/batches",
            {"dataset_id": "test", "batch_id": "batch", "producer_version": "draft"},
        ),
        ("GET", "/api/v1/runs/run?dataset_id=test", None),
        ("POST", "/api/v1/graph/query", {"dataset_id": "test", "query_type": "reserved"}),
        ("GET", "/api/v1/evidence/item?dataset_id=test", None),
    ],
)
async def test_reserved_business_endpoints_report_unimplemented(client, method, path, body):
    response = await client.request(method, path, json=body)
    assert response.status_code == 501
    assert response.json()["error"] == "FeatureNotImplemented"
    schema_path = path.split("?")[0]
    if "/runs/" in schema_path:
        schema_path = "/api/v1/runs/{run_id}"
    elif "/evidence/" in schema_path:
        schema_path = "/api/v1/evidence/{evidence_id}"
    operation = (await client.get("/openapi.json")).json()["paths"][schema_path][method.lower()]
    error_schema = operation["responses"]["501"]["content"]["application/json"]["schema"]
    assert error_schema["$ref"] == "#/components/schemas/ErrorResponse"


async def test_unavailable_dependency_affects_readiness_and_is_closed():
    class UnavailableStorage:
        closed = False

        async def ready(self):
            return False

        async def close(self):
            self.closed = True

    storage = UnavailableStorage()
    services = ApplicationServices(
        pipeline=Pipeline(), query=QueryService(), storage=storage, graph_backend="neo4j"
    )
    app = create_app(services=services)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/ready")).status_code == 503
    assert storage.closed


async def test_liveness_stays_responsive_while_dependency_check_is_pending():
    entered = asyncio.Event()

    class PendingStorage:
        async def ready(self):
            entered.set()
            await asyncio.Event().wait()

        async def close(self):
            pass

    services = ApplicationServices(
        pipeline=Pipeline(), query=QueryService(), storage=PendingStorage(), graph_backend="neo4j"
    )
    app = create_app(services=services)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        pending = asyncio.create_task(client.get("/ready"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            response = await asyncio.wait_for(client.get("/health"), timeout=1)
            assert response.status_code == 200
            assert not pending.done()
        finally:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
