"""Check the foundation and the removal of previous business endpoints."""

import pytest
from fastapi.testclient import TestClient

from bank_project.main import create_app
from bank_project.settings import Settings


def test_health_and_schema_without_external_services(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "unused"))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok", "version": "0.1.0"}
        assert client.get("/ready").json() == {"status": "ready"}
        paths = set(client.get("/openapi.json").json()["paths"])
        assert {"/health", "/ready", "/api/v1/intake/uploads"} <= paths
        assert client.get("/docs").status_code == 200
    assert (tmp_path / "unused" / "intake" / "batches.sqlite3").exists()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/imports",
        "/api/v1/imports/upload",
        "/api/v1/extractions",
        "/api/v1/alignment/plans",
        "/api/v1/workspace/batches",
        "/api/v1/pipeline",
    ],
)
def test_removed_business_routes_are_unavailable(path, tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        assert client.get(path).status_code == 404
        assert client.post(path, json={}).status_code == 404


def test_secrets_remain_private_and_mysql_configuration_is_preserved():
    settings = Settings(
        _env_file=None,
        mysql_port=3307,
        mysql_password="private-password",
        model_api_key="private-key",
    )
    assert settings.mysql_port == 3307
    assert settings.mysql_password.get_secret_value() == "private-password"
    assert "private-password" not in repr(settings)
    assert "private-key" not in settings.model_dump_json()
