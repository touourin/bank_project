"""Observable matching progress and isolated, validated human revisions."""

import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from test_alignment import FakeModel, FakeRetriever, analyze, catalog_bytes, ignore_progress, source

from bank_project.alignment.analyzer import Analyzer
from bank_project.alignment.catalog import Catalog
from bank_project.alignment.editing import revise_mapping
from bank_project.alignment.models import AlignmentError, MappingEditRequest, MappingResult
from bank_project.alignment.store import RunStore
from bank_project.main import create_app
from bank_project.settings import Settings


def test_trace_checkpoints_preserve_real_candidates_sampling_and_model_correction():
    snapshots = []

    async def save(message, result):
        snapshots.append(MappingResult.model_validate_json(result.model_dump_json()))

    s = source(values=[[str(i), "甲"] for i in range(11)])
    result = asyncio.run(
        Analyzer(FakeModel(), FakeRetriever()).analyze(
            [s],
            Catalog(catalog_bytes()),
            ignore_progress,
            save,
        )
    )
    first = snapshots[0].tables[0]
    assert first.trace.steps[0].status == "running"
    assert first.trace.meaning is None and first.trace.candidates == []
    during_selection = next(
        v.tables[0] for v in snapshots if v.tables[0].trace.steps[2].status == "running"
    )
    assert during_selection.trace.meaning.meaning == "每行代表一位客户"
    assert [c.id for c in during_selection.trace.candidates] == ["customer"]
    trace = result.tables[0].trace
    assert trace.sample_rows == [2, 4, 7, 9, 12]
    assert trace.selection_attempts == 0
    assert trace.method == "retrieve"
    assert trace.retrievals[0].selected.score == 0.93
    assert trace.selected.id == "customer"
    assert all(s.status == "completed" for s in trace.steps)
    assert "invented" not in trace.model_dump_json()


def test_failed_and_empty_tables_never_display_completed_stages():
    failed = analyze(FakeModel(concept_id="invented")).tables[0]
    assert [s.status for s in failed.trace.steps] == ["failed", "skipped", "skipped", "skipped"]
    assert failed.trace.selected is None
    empty = analyze(s=source(values=[])).tables[0]
    assert all(s.status == "skipped" for s in empty.trace.steps)
    assert empty.trace.meaning is None


def test_no_candidates_is_distinct_from_model_failure():
    table = analyze(retriever=FakeRetriever(node_id=None)).tables[0]
    assert table.status == "unmatched"
    assert all(s.status == "completed" for s in table.trace.steps)
    assert table.trace.retrievals[0].status == "unmatched"


def test_parent_ids_and_search_are_scoped_to_the_recorded_catalog():
    content = json.loads(catalog_bytes(("r1", "r2")))
    content["graph"]["relationships"] = [
        {
            "type": "IS_A",
            "start": "Concept:r1:customer",
            "end": "Concept:r1:account",
            "properties": {"dataset_revision": "r1"},
        },
        {
            "type": "IS_A",
            "start": "Concept:r2:account",
            "end": "Concept:r2:customer",
            "properties": {"dataset_revision": "r2"},
        },
    ]
    catalog = Catalog(json.dumps(content).encode(), "r1")
    node = catalog.search("customer")[0]
    assert [(p.id, p.name) for p in node.parents] == [("account", "账户")]
    assert catalog.describe("account").parents == []
    assert catalog.search("不存在的名词") == []


def test_manual_changes_preserve_model_trace_confidence_fields_and_provenance():
    original = analyze(retriever=FakeRetriever(score=0.4))
    catalog = Catalog(catalog_bytes())
    edit = MappingEditRequest(table_id="customers", concept_id="account", reason="人工确认业务含义")
    revised = revise_mapping(original, edit, catalog, [source()])
    old, new = original.tables[0], revised.tables[0]
    assert old.status == "review" and old.concept_id == "customer" and not old.manual_edits
    assert new.status == "mapped" and new.verification == "manual" and new.concept_id == "account"
    assert old.trace == new.trace and new.trace.selected.id == "customer"
    assert new.confidence == 0.4 and old.columns == new.columns
    assert new.manual_edits[0].before.id == "customer"
    assert new.manual_edits[0].after.id == "account"
    # Attribute edits do not silently approve the whole table or alter structural keys.
    field = revise_mapping(
        original, edit.model_copy(update={"column": "name"}), catalog, [source()]
    )
    assert field.tables[0].status == "review"
    assert field.tables[0].columns[1].concept_id == "account"
    cleared = revise_mapping(
        field, edit.model_copy(update={"column": "name", "concept_id": None}), catalog, [source()]
    )
    assert cleared.tables[0].columns[1].concept_id is None
    assert cleared.tables[0].columns[1].property_key == "field_001"


@pytest.mark.parametrize(
    "changes", [{"concept_id": "invented"}, {"table_id": "missing"}, {"column": "missing"}]
)
def test_manual_edits_reject_invented_nodes_tables_and_columns(changes):
    request = MappingEditRequest(table_id="customers", concept_id="account", reason="确认")
    with pytest.raises(AlignmentError):
        revise_mapping(
            analyze(), request.model_copy(update=changes), Catalog(catalog_bytes()), [source()]
        )


def test_review_api_forks_published_run_keeps_graph_and_survives_restart(tmp_path):
    path = tmp_path / "ontology.json"
    path.write_bytes(catalog_bytes())
    settings = Settings(_env_file=None, data_dir=tmp_path, ontology_snapshot=path)
    app = create_app(settings)
    with TestClient(app) as client:
        store = app.state.alignment.store
        original, seq, owner = store.create([source()])
        original = store.update(
            original.id,
            seq,
            owner,
            finish=True,
            status="ready",
            result=analyze().model_dump(),
            graph_status="ready",
            graph_version="published-version",
        )
        base = f"/api/v1/alignment/runs/{original.id}"
        assert client.get(base + "/concepts?q=customer").json()[0]["id"] == "customer"
        payload = {"table_id": "customers", "concept_id": "account", "reason": "人工核对"}
        response = client.post(base + "/mapping", json=payload)
        assert response.status_code == 201
        updated = response.json()
        assert updated["based_on_run_id"] == original.id
        assert updated["graph_status"] == "none" and updated["graph_version"] is None
        assert store.get(original.id) == original
        assert store.sources(updated["id"]) == store.sources(original.id)
        assert (
            client.post(base + "/mapping", json={**payload, "concept_id": "invented"}).status_code
            == 422
        )
        assert client.post(base + "/mapping", json={**payload, "reason": " "}).status_code == 422
        assert (
            client.post(base + "/mapping", json={**payload, "concept_id": None}).status_code == 422
        )
        assert len(store.list()) == 2
        # A changed snapshot may not silently reinterpret an older run.
        path.write_bytes(path.read_bytes() + b" ")
        assert client.post(base + "/mapping", json=payload).status_code == 409
        assert client.get(base + "/concepts").status_code == 409
    with TestClient(create_app(settings)) as client:
        restored = client.get(f"/api/v1/alignment/runs/{updated['id']}").json()
        assert restored == updated


def test_active_graph_build_cannot_be_revised(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    run, seq, owner = store.create([source()])
    store.update(run.id, seq, owner, finish=True, status="ready", result=analyze().model_dump())
    store.start_graph(run.id)
    with pytest.raises(AlignmentError, match="等待"):
        store.revise(run.id, analyze())


def test_api_exposes_durable_progress_before_model_selection_finishes(tmp_path):
    release = threading.Event()

    class PausedRetriever(FakeRetriever):
        async def search_many(self, queries):
            while not release.is_set():
                await asyncio.sleep(0.01)
            return await super().search_many(queries)

    path = tmp_path / "ontology.json"
    path.write_bytes(catalog_bytes())
    settings = Settings(
        _env_file=None, data_dir=tmp_path, ontology_snapshot=path, model_api_key="test"
    )
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.alignment.analyzer.model = FakeModel()
        app.state.alignment.analyzer.retriever = PausedRetriever()
        batch = client.post(
            "/api/v1/intake/uploads?filename=customer.csv", content=b"id,name\n001,Alpha"
        ).json()
        run = client.post(
            "/api/v1/alignment/runs",
            json={"tables": [{"batch_id": batch["id"], "table_id": batch["tables"][0]["id"]}]},
        ).json()
        url = f"/api/v1/alignment/runs/{run['id']}"
        try:
            for _ in range(100):
                current = client.get(url).json()
                if (
                    current["result"]
                    and current["result"]["tables"][0]["trace"]["steps"][1]["status"] == "running"
                ):
                    break
                time.sleep(0.01)
            assert current["status"] == "analyzing"
            trace = current["result"]["tables"][0]["trace"]
            assert trace["meaning"]["meaning"] == "每行代表一位客户"
            assert trace["candidates"] == [] and trace["selected"] is None
            # A separate store reads the checkpoint; it is not only in process memory.
            assert (
                RunStore(app.state.alignment.store.path)
                .get(run["id"])
                .result.tables[0]
                .trace.meaning
                is not None
            )
            assert (
                client.post(
                    url + "/mapping",
                    json={
                        "table_id": batch["tables"][0]["id"],
                        "concept_id": "customer",
                        "reason": "提前修改",
                    },
                ).status_code
                == 409
            )
        finally:
            release.set()
        for _ in range(100):
            current = client.get(url).json()
            if current["status"] == "ready":
                break
            time.sleep(0.01)
        assert current["result"]["tables"][0]["trace"]["selected"]["id"] == "customer"
