"""Remote topology and WHY must agree before becoming generation evidence."""

import asyncio
import copy
import hashlib
import json
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_risk_service import WHY, Model, snapshot

from bank_project.alignment.models import AlignmentError
from bank_project.main import create_app
from bank_project.ontology.service import OntologyService
from bank_project.risk.catalog import RiskCatalog
from bank_project.risk.models import PropagationRequest
from bank_project.risk.propagation import canonical_json, propagate
from bank_project.risk.service import RiskService
from bank_project.settings import Settings

REVISION = "r1"
ONTOLOGY = "ontology-1"
ROOT = "http://ontology.test/v2/ontologies/ontology-1"
DIGEST = hashlib.sha256(canonical_json(WHY).encode()).hexdigest()


@pytest.fixture
def remote(tmp_path):
    local = tmp_path / "local.json"
    snapshot(local)
    graph = json.loads(local.read_bytes())["graph"]
    export = {
        "ontology_id": ONTOLOGY,
        "dataset_revision": REVISION,
        "nodes": [
            {
                "node_id": p["node_id"],
                "node_name": p["node_name"],
                "node_semantic_type": "属性型BO" if p["node_id"] == "limit" else "过程型BO",
                "nonempty_dimensions": ["why"] if p.get("why") else [],
                "dimension_hashes": {"why": DIGEST} if p.get("why") else {},
            }
            for node in graph["nodes"]
            if node["label"] == "Concept"
            for p in [node["properties"]]
        ],
        "relations": [
            {
                "source_node_id": edge["start"].rsplit(":", 1)[-1],
                "target_node_id": edge["end"].rsplit(":", 1)[-1],
                "relation_type": edge["type"],
            }
            for edge in graph["relationships"]
        ],
    }
    response = {
        "ontology_id": ONTOLOGY,
        "dataset_revision": REVISION,
        "node_id": "limit",
        "dimensions": {"why": WHY},
        "dimension_hashes": {"why": DIGEST},
    }
    state = SimpleNamespace(
        export=export, response=response, calls=Counter(), status=200, revision=REVISION
    )

    def handle(request):
        path = request.url.path.removeprefix("/v2/ontologies/ontology-1")
        state.calls[path] += 1
        if state.status != 200:
            return httpx.Response(state.status)
        if path == "/ready":
            return httpx.Response(
                200,
                json={"ontology_id": ONTOLOGY, "dataset_revision": state.revision, "ready": True},
            )
        if path == "/evaluation-snapshot":
            assert request.url.params["expected_revision"] == REVISION
            return httpx.Response(200, json=state.export)
        assert path == "/concept/dimensions"
        payload = json.loads(request.content)
        assert payload["node_id"] == "limit" and payload["expected_revision"] == REVISION
        assert set(payload["dimensions"]) <= {"what", "why"}
        response = copy.deepcopy(state.response)
        if "what" in payload["dimensions"]:
            response["dimensions"].setdefault("what", None)
        return httpx.Response(200, json=response)

    state.settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        ontology_snapshot=local,
        ontology_revision=REVISION,
        risk_ontology_base_url=ROOT,
    )
    state.source = OntologyService(state.settings, transport=httpx.MockTransport(handle))
    return state


def test_remote_catalog_has_real_why_and_preserves_local_file(remote):
    original = remote.settings.ontology_snapshot.read_bytes()
    catalog = remote.source.for_risk()
    assert len(catalog.names) == 4
    assert catalog.why_by_id == {"limit": WHY}
    assert catalog.search("limit")[0]["semantic_type"] == "属性型BO"
    assert propagate(catalog.graph, "cash")["candidates"][0]["node_id"] == "limit"
    assert remote.source.for_risk() is catalog
    assert remote.calls == {"/ready": 2, "/evaluation-snapshot": 1, "/concept/dimensions": 1}
    assert remote.settings.ontology_snapshot.read_bytes() == original
    coverage = json.loads(catalog.content)["risk_why_import"]
    assert coverage["coverage_complete"] and coverage["empty_why_node_count"] == 3


@pytest.mark.parametrize("status", [409, 503])
def test_cached_catalog_does_not_mask_remote_failure(remote, status):
    remote.source.for_risk()
    remote.status = status
    with pytest.raises(AlignmentError):
        remote.source.for_risk()


def test_ready_revision_must_match_request_even_with_cache(remote):
    remote.source.for_risk()
    remote.revision = "another-revision"
    with pytest.raises(AlignmentError, match="版本不一致"):
        remote.source.for_risk()


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.response.update(ontology_id="other"),
        lambda r: r.response.update(dataset_revision="other"),
        lambda r: r.response.update(node_id="other"),
        lambda r: r.response.update(dimensions={"why": [{"text": "tampered"}]}),
        lambda r: r.export["nodes"][0].update(dimension_hashes={"why": "0" * 64}),
        lambda r: r.export["nodes"][0].update(nonempty_dimensions=[]),
        lambda r: r.export.update(dataset_revision="other"),
        lambda r: r.export["relations"][0].update(target_node_id="missing"),
    ],
)
def test_inconsistent_response_never_publishes_partial_cache(remote, change):
    change(remote)
    with pytest.raises(AlignmentError):
        remote.source.for_risk()
    assert remote.source._risk_catalog is None


def test_generation_pins_remote_evidence_for_later_offline_review(remote):
    service = RiskService(remote.settings, SimpleNamespace(configured=False), model=Model())
    service.ontology = remote.source
    assert service.catalog()["source"]["kind"] == "remote"
    assert service.catalog()["source"]["why_node_count"] == 1

    async def generate():
        job = await service.start(PropagationRequest(anchor_node_ids=["cash"]))
        await asyncio.gather(*service.tasks)
        return service.store.get("jobs", job["id"])

    job = asyncio.run(generate())
    assert job["status"] == "succeeded" and len(job["case_ids"]) == 1
    remote.status = 503
    pinned = RiskCatalog.load(service.snapshots / f"{job['snapshot_sha256']}.json", REVISION)
    assert pinned.why_by_id["limit"] == WHY
    assert job["coverage"]["why_import"]["source_type"] == "ontology_api"


def test_no_remote_configuration_keeps_local_mode(remote):
    settings = copy.copy(remote.settings)
    settings.ontology_base_url = settings.retrieve_base_url = settings.risk_ontology_base_url = None
    catalog = OntologyService(settings).for_risk()
    assert catalog.why_by_id["limit"] == WHY
    assert not remote.calls


def test_common_source_supports_browsing_and_matching_without_local_file(remote, monkeypatch):
    remote.settings.ontology_snapshot.unlink()
    monkeypatch.setattr("bank_project.main.OntologyService", lambda _: remote.source)
    app = create_app(remote.settings)
    with TestClient(app) as client:
        assert (
            app.state.alignment.ontology is app.state.knowledge.ontology is app.state.risk.ontology
        )
        response = client.get("/api/v1/ontology/graph")
        assert response.status_code == 200
        result = response.json()
        source = result["source"]
        assert source["ontology_id"] == ONTOLOGY and source["why_node_count"] == 1
        limit = next(n for n in result["graph"]["nodes"] if n["id"] == "limit")
        assert limit["type"] == "属性型BO" and limit["properties"]["has_why"]
        assert client.get("/api/v1/alignment/config").json()["ontology_source"] == source
        assert app.state.risk.catalog()["source"] == source
        assert remote.calls["/concept/dimensions"] == 0
        detail = client.get(
            "/api/v1/ontology/concepts/limit/dimensions",
            params={"revision": REVISION, "snapshot_sha256": source["snapshot_sha256"]},
        )
        assert detail.json() == {"what": None, "why": WHY}
        remote.status = 503
        assert client.get("/api/v1/ontology/graph").status_code == 503
        assert remote.source.pinned(REVISION, source["snapshot_sha256"], ONTOLOGY).names


def test_history_survives_restart_with_new_remote_revision_and_legacy_bootstrap(remote):
    current = remote.source.current()
    legacy_digest = hashlib.sha256(remote.settings.ontology_snapshot.read_bytes()).hexdigest()
    settings = remote.settings.model_copy(update={"ontology_revision": "r2"})
    restored = OntologyService(settings)
    assert restored.pinned(REVISION, current.sha256, ONTOLOGY).names == current.names
    assert restored.pinned(REVISION, legacy_digest).names == current.names
    with pytest.raises(AlignmentError, match="ID 不一致"):
        restored.pinned(REVISION, current.sha256, "another-ontology")
    remote.source.snapshots.path(legacy_digest).unlink()
    # First startup after migration must archive the old source, even with a new revision.
    restored = OntologyService(settings)
    assert restored.pinned(REVISION, legacy_digest).names == current.names


def test_ontology_routes_require_the_same_authentication_as_business_apis(remote):
    settings = remote.settings.model_copy(
        update={"api_token": Settings(_env_file=None, api_token="a" * 24).api_token}
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/ontology/graph").status_code == 401


@pytest.mark.parametrize("legacy_key", ["retrieve_base_url", "risk_ontology_base_url"])
def test_legacy_address_is_an_alias_for_one_authority(legacy_key):
    settings = Settings(_env_file=None, **{legacy_key: ROOT})
    assert (
        settings.ontology_base_url
        == settings.retrieve_base_url
        == settings.risk_ontology_base_url
        == ROOT
    )
    with pytest.raises(ValueError, match="同一本体地址"):
        Settings(_env_file=None, ontology_base_url=ROOT, **{legacy_key: ROOT + "-other"})
