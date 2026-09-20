"""Resolution APIs retain application auth, origin, bounds and revision contracts."""

from fastapi.testclient import TestClient
from test_resolution import ready, source

from bank_project.main import create_app
from bank_project.settings import Settings


def test_review_export_auth_and_stale_conflict(tmp_path):
    service, run = ready(tmp_path / "resolution")
    config = Settings(
        _env_file=None, data_dir=tmp_path / "app", api_token="resolution-api-token-1234567890"
    )
    headers = {"Authorization": "Bearer resolution-api-token-1234567890"}
    with TestClient(create_app(config)) as client:
        client.app.state.resolution = service
        root = "/api/v1/resolution/runs"
        assert client.get(root).status_code == 401
        assert (
            client.get(root, headers={**headers, "Origin": "https://example.invalid"}).status_code
            == 403
        )
        listing = client.get(root, headers=headers).json()
        assert listing[0]["id"] == run.id and listing[0]["candidates"] == []
        detail = client.get(f"{root}/{run.id}", headers=headers).json()
        pair = next(row for row in detail["candidates"] if set(row["node_ids"]) == {"a", "b"})
        decision = {
            "candidate_id": pair["id"],
            "action": "merge",
            "expected_revision": 0,
            "canonical_id": "b",
        }
        response = client.post(f"{root}/{run.id}/decisions", json=decision, headers=headers)
        assert response.status_code == 200 and response.json()["summary"]["node_count"] == 2
        assert (
            client.post(f"{root}/{run.id}/decisions", json=decision, headers=headers).status_code
            == 409
        )
        result = client.get(f"{root}/{run.id}/graph", headers=headers)
        assert result.status_code == 200 and len(result.json()["nodes"]) == 2
        original = client.get(f"{root}/{run.id}/original", headers=headers).json()
        assert len(original["nodes"]) == len(source()["nodes"]) == 3
        assert (
            client.post(
                root, json={"source_kind": "unexpected", "source_id": "test"}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"{root}/{run.id}/decisions",
                json={**decision, "note": "x" * 70_000},
                headers=headers,
            ).status_code
            == 413
        )
