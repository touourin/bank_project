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
        source = {"source_kind": "graphrag", "source_id": "dataset"}
        assert client.get(root + "/graph", params=source).status_code == 401
        original = client.get(root + "/graph", params=source, headers=headers)
        assert original.status_code == 200
        assert original.json()["nodes"][0]["properties"]["unknown"]["code"] == "001"
        assert client.get(root + "/matches", headers=headers).json() == []
        assert (
            client.get(
                root + "/graph",
                params={"source_kind": "database", "source_id": "raw-db"},
                headers=headers,
            ).status_code
            == 422
        )
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
        source["source_id"] = f"match:{run_id}:1"
        assert (
            client.get(root + "/graph", params=source, headers=headers).json()["nodes"]
            == graph["nodes"]
        )
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
        assert client.get(root + "/graph", params=source, headers=headers).status_code == 409
        source["source_id"] = f"match:{run_id}:2"
        assert (
            client.get(root + "/graph", params=source, headers=headers).json()["nodes"][0]["boid"]
            == "account"
        )
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


def test_accept_proposals_api_validates_revision_auth_and_returns_reviewable_results(tmp_path):
    configured = service(tmp_path, score=0.177)
    configured.settings.api_token = SecretStr("knowledge-test-token-1234567890")
    app = create_app(configured.settings)
    with TestClient(app) as client:
        app.state.knowledge.retriever = configured.retriever
        app.state.knowledge.graphrag = configured.graphrag
        headers = {"Authorization": "Bearer knowledge-test-token-1234567890"}
        root = "/api/v1/knowledge/matches"
        response = client.post(
            root, json={"source_kind": "graphrag", "source_id": "dataset"}, headers=headers
        )
        run_id = response.json()["id"]
        for _ in range(100):
            run = client.get(f"{root}/{run_id}", headers=headers).json()
            if run["status"] != "running":
                break
            time.sleep(0.01)
        assert run["status"] == "ready" and run["summary"]["matched_nodes"] == 0
        endpoint = f"{root}/{run_id}/accept-proposals"
        payload = {"expected_revision": 1, "reviewer": "验收人", "note": "已核对"}
        assert client.post(endpoint, json=payload).status_code == 401
        assert client.post(endpoint, json={}, headers=headers).status_code == 422
        assert (
            client.post(endpoint, json={"expected_revision": 0}, headers=headers).status_code == 422
        )
        assert (
            client.post(
                endpoint, json=payload, headers={**headers, "Origin": "https://invalid.example"}
            ).status_code
            == 403
        )
        accepted = client.post(endpoint, json=payload, headers=headers)
        assert accepted.status_code == 200
        result = accepted.json()
        assert result["revision"] == 2 and result["summary"]["matched_nodes"] == 2
        assert result["summary"]["matched_edges"] == 2 and len(result["audits"]) == 4
        assert result["nodes"][0]["trace"]["selected"]["score"] == 0.177
        assert "graph" not in result
        assert client.post(endpoint, json=payload, headers=headers).status_code == 409
        exported = client.get(f"{root}/{run_id}/graph", headers=headers).json()
        assert exported["nodes"][0]["boid"] == "customer"
        assert exported["edges"][1]["edge_type"] == "OWNS"
