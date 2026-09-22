"""Public risk endpoints use app auth, limits and optimistic review controls."""

import time
from uuid import uuid4

from fastapi.testclient import TestClient
from test_risk_service import Executor, Model, snapshot

from bank_project.main import create_app
from bank_project.settings import Settings


def test_risk_api_generate_review_execute_auth_and_bounds(tmp_path):
    path = tmp_path / "snapshot.json"
    snapshot(path)
    token = "risk-test-token-123456789012345"
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        ontology_snapshot=path,
        ontology_revision="r1",
        api_token=token,
    )
    headers = {"Authorization": f"Bearer {token}"}
    root = "/api/v1/risk"
    with TestClient(create_app(settings)) as client:
        risk = client.app.state.risk
        risk.model = Model()
        risk.executor = Executor()
        assert client.get(root + "/cases").status_code == 401
        assert (
            client.get(
                root + "/catalog", headers={**headers, "Origin": "https://bad.invalid"}
            ).status_code
            == 403
        )
        catalog = client.get(root + "/catalog?q=现金", headers=headers)
        assert catalog.status_code == 200
        assert catalog.json()["revision"] == "r1"
        assert any(row["id"] == "limit" and row["has_why"] for row in catalog.json()["nodes"])
        assert (
            client.post(
                root + "/propagations", json={"anchor_node_ids": []}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post(
                root + "/propagations",
                json={"anchor_node_ids": ["cash"], "brief": "x" * 70000},
                headers=headers,
            ).status_code
            == 413
        )
        response = client.post(
            root + "/propagations", json={"anchor_node_ids": ["cash"]}, headers=headers
        )
        assert response.status_code == 202
        identifier = response.json()["id"]
        for _ in range(100):
            job = client.get(root + "/propagations/" + identifier, headers=headers).json()
            if job["status"] != "running":
                break
            time.sleep(0.01)
        assert job["status"] == "succeeded"
        case = client.get(root + "/cases/" + job["case_ids"][0], headers=headers).json()
        base = f"{root}/cases/{case['id']}"
        version = {"expected_version": case["version"], "expected_hash": case["content_hash"]}
        payload = {
            **version,
            "graph_version": str(uuid4()),
            "field_mapping": {"amount": "金额"},
            "start": "2026-01-01T00:00:00+08:00",
            "end": "2026-01-02T00:00:00+08:00",
        }
        assert client.post(base + "/executions", json=payload, headers=headers).status_code == 409
        approved = client.post(
            base + "/review",
            json={**version, "action": "approve", "evidence_confirmed": True},
            headers=headers,
        )
        assert approved.status_code == 200 and approved.json()["version"] == 2
        assert (
            client.post(
                base + "/review", json={**version, "action": "reject"}, headers=headers
            ).status_code
            == 409
        )
        payload["expected_version"] = 2
        response = client.post(base + "/executions", json=payload, headers=headers)
        assert response.status_code == 200 and response.json()["status"] == "succeeded"
        assert len(client.get(base + "/executions", headers=headers).json()) == 1
        payload["start"] = "2026-01-01"
        assert client.post(base + "/executions", json=payload, headers=headers).status_code == 422
        payload["bo_scope"] = ["neighbor"]
        assert client.post(base + "/executions", json=payload, headers=headers).status_code == 422
