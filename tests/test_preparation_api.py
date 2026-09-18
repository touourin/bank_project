import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from bank_project.main import create_app
from bank_project.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client(tmp_path):
    (tmp_path / "plain.csv").write_text("item,value\n0001,测试\n")
    app = create_app(Settings(data_dir=tmp_path / "data", import_root=tmp_path, _env_file=None))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client


async def test_two_steps_are_independent_and_persist_results(client):
    response = await client.post(
        "/api/v1/imports",
        json={
            "dataset_id": "demo",
            "batch_id": "b1",
            "source_system": "erp",
            "source_uri": "file:plain.csv",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["records"] == 1
    assert (await client.get("/api/v1/extractions/b1/status?dataset_id=demo")).json()[
        "status"
    ] == "not_started"
    response = await client.post(
        "/api/v1/extractions", json={"dataset_id": "demo", "batch_id": "b1"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["entities"] == 1
    result = await client.get("/api/v1/extractions/b1?dataset_id=demo")
    assert result.json()["entities"][0]["properties"] == {"item": "0001", "value": "测试"}
    assert (await client.get("/api/v1/extractions/b1/status?dataset_id=demo")).json()[
        "status"
    ] == "completed"
    assert (await client.get("/api/v1/imports/b1?dataset_id=another")).status_code == 404


async def test_upload_and_disabled_model_report_real_failure(client):
    response = await client.post(
        "/api/v1/imports/upload",
        data={"dataset_id": "docs", "batch_id": "one", "source_system": "files"},
        files={"file": ("说明.txt", "设备已经检修完成。".encode(), "text/plain")},
    )
    assert response.status_code == 200, response.text
    assert (
        await client.post("/api/v1/extractions", json={"dataset_id": "docs", "batch_id": "one"})
    ).status_code == 503
    status = await client.get("/api/v1/extractions/one/status?dataset_id=docs")
    assert status.json()["status"] == "failed"
    assert "模型未配置" in status.json()["detail"]
    assert (await client.get("/api/v1/extractions/one?dataset_id=docs")).status_code == 404


async def test_error_codes_and_input_validation(client):
    request = {
        "dataset_id": "d",
        "batch_id": "b",
        "source_system": "s",
        "source_uri": "file:../private.csv",
    }
    response = await client.post("/api/v1/imports", json=request)
    assert response.status_code == 422
    assert response.json()["error"] == "IntakeError"
    request["source_uri"] = "https://example.com/ssrf"
    assert (await client.post("/api/v1/imports", json=request)).status_code == 422
    request["source_uri"] = "mysql:some_table"
    assert (await client.post("/api/v1/imports", json=request)).status_code == 503
    assert (
        await client.post("/api/v1/extractions", json={"dataset_id": "d", "batch_id": "not-found"})
    ).status_code == 404
    response = await client.post(
        "/api/v1/imports/upload",
        data={"dataset_id": "d", "batch_id": "b", "source_system": "s"},
        files={"file": ("bad.csv", b"a,a\n1,2")},
    )
    assert response.status_code == 422
    assert (await client.get("/api/v1/imports/b?dataset_id=d")).status_code == 404


async def test_token_is_checked_before_reading_upload_and_docs_have_auth(tmp_path):
    token = "a-test-token-with-at-least-24-characters"
    app = create_app(Settings(api_token=token, data_dir=tmp_path, _env_file=None))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/api/v1/imports/none?dataset_id=d")
        assert response.status_code == 401
        assert (await client.get("/health")).status_code == 200
        response = await client.get(
            "/api/v1/imports/none?dataset_id=d", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 404
        operation = (await client.get("/openapi.json")).json()["paths"]["/api/v1/imports"]["post"]
        assert operation["security"] == [{"HTTPBearer": []}]
        assert token not in (await client.get("/openapi.json")).text


async def test_oversized_content_length_and_chunked_body_are_rejected(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, max_file_bytes=1024, _env_file=None))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post(
            "/api/v1/imports", content=b"{}", headers={"content-length": str(2 * 1024 * 1024)}
        )
        assert response.status_code == 413

        async def body():
            for _ in range(32):
                yield b"x" * 65536

        response = await client.post(
            "/api/v1/imports", content=body(), headers={"content-type": "application/json"}
        )
        assert response.status_code == 413, response.text


async def test_conversion_does_not_block_liveness(client, monkeypatch):
    service = client._transport.app.state.services.extraction
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    import threading

    release = threading.Event()
    original = service.extract

    def wait_for_test(request):
        loop.call_soon_threadsafe(entered.set)
        release.wait(timeout=5)
        return original(request)

    monkeypatch.setattr(service, "extract", wait_for_test)
    conversion = asyncio.create_task(
        client.post("/api/v1/extractions", json={"dataset_id": "d", "batch_id": "b"})
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert (await asyncio.wait_for(client.get("/health"), timeout=1)).status_code == 200
    finally:
        release.set()
        await conversion


async def test_busy_service_rejects_before_upload_and_releases_capacity(client, monkeypatch):
    import threading

    app = client._transport.app
    app.state.max_concurrent_jobs = 1
    service = app.state.services.extraction
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = service.extract

    def wait_for_test(request):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=5)
        return original(request)

    async def unread_body():
        pytest.fail("An overloaded service must not read another upload")
        yield b""

    monkeypatch.setattr(service, "extract", wait_for_test)
    conversion = asyncio.create_task(
        client.post("/api/v1/extractions", json={"dataset_id": "d", "batch_id": "one"})
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        response = await client.post("/api/v1/imports/upload", content=unread_body())
        assert response.status_code == 503
        assert response.headers["retry-after"] == "1"
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/api/v1/mappings")).status_code == 200
    finally:
        release.set()
        await conversion
    # A failed job must release its slot, too.
    response = await client.post("/api/v1/extractions", json={"dataset_id": "d", "batch_id": "two"})
    assert response.status_code == 404


async def test_upload_uses_configured_file_limit_and_leaves_no_import(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, max_file_bytes=1024, _env_file=None))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post(
            "/api/v1/imports/upload",
            data={"dataset_id": "d", "batch_id": "b", "source_system": "s"},
            files={"file": ("big.txt", b"x" * 1025)},
        )
        assert response.status_code == 413
        assert (await client.get("/api/v1/imports/b?dataset_id=d")).status_code == 404
