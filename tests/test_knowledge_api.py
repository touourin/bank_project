"""Exercise the public matching API through the application's actual dependency wiring."""

import time

from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_knowledge_matching import service

from bank_project.main import create_app


def test_matching_api_auth_review_and_complete_graph(tmp_path):
    configured = service(tmp_path)
    configured.settings.api_token = SecretStr("knowledge-test-token-1234567890")
    app = create_app(configured.settings)
    with TestClient(app) as client:
        app.state.knowledge.retriever = configured.retriever
        app.state.knowledge.graphrag = configured.graphrag
        root = "/api/v1/knowledge"
        assert client.get(root + "/sources").status_code == 401
        headers = {"Authorization": "Bearer knowledge-test-token-1234567890"}
        assert client.get(root + "/sources", headers=headers).json()[0]["id"] == "dataset"
        response = client.post(
            root + "/matches",
            json={"source_kind": "graphrag", "source_id": "dataset"},
            headers=headers,
        )
        assert response.status_code == 202
        run_id = response.json()["id"]
        for _ in range(100):
            run = client.get(root + "/matches/" + run_id, headers=headers).json()
            if run["status"] != "running":
                break
            time.sleep(0.01)
        assert run["status"] == "ready"
        graph = client.get(f"{root}/matches/{run_id}/graph", headers=headers).json()
        assert graph["nodes"][0]["properties"]["unknown"]["code"] == "001"
        assert graph["nodes"][0]["boid"] == "customer"
        decision = {"target": "node", "target_id": "a", "boid": "account", "expected_revision": 1}
        assert (
            client.post(
                f"{root}/matches/{run_id}/decisions",
                json=decision,
                headers={**headers, "Origin": "https://invalid.example"},
            ).status_code
            == 403
        )
        revised = client.post(f"{root}/matches/{run_id}/decisions", json=decision, headers=headers)
        assert revised.status_code == 200
        assert revised.json()["revision"] == 2
        assert (
            client.post(
                f"{root}/matches/{run_id}/decisions", json=decision, headers=headers
            ).status_code
            == 409
        )
        assert (
            client.get(f"{root}/matches/{run_id}/concepts?q=账户", headers=headers).status_code
            == 200
        )
        assert client.get(root + "/matches", headers=headers).json()[0]["nodes"] == []
