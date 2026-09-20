"""Complete proposals and one-step adoption preserve uncertainty and immutable history."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_alignment import FakeModel, FakeRetriever, catalog_bytes, ignore_progress, source

from bank_project.alignment.analyzer import Analyzer
from bank_project.alignment.catalog import Catalog
from bank_project.alignment.models import AlignmentError
from bank_project.alignment.planning import retrieval_query
from bank_project.alignment.retrieval import RetrievalResult
from bank_project.alignment.store import RunStore
from bank_project.alignment.templates import validate_template
from bank_project.main import create_app
from bank_project.settings import Settings


def proposal(model=None, retriever=None, src=None):
    return asyncio.run(
        Analyzer(
            model or FakeModel(), retriever or FakeRetriever(), relation_index=object()
        ).analyze([src or source()], Catalog(catalog_bytes()), ignore_progress)
    )


@pytest.mark.parametrize("name", ["数据", "数据表", "Sheet1", "sheet_2", "工作表 3"])
def test_generic_sheet_uses_business_query_and_file_context(name):
    src, model, retrieve = source(), FakeModel(), FakeRetriever()
    src.table.name = name
    src.batch.name = "客户旅程.xlsx"
    result = proposal(model, retrieve, src)
    assert retrieve.queries[0] == "客户"
    assert json.loads(model.calls[0][1])["source_name"] == "客户旅程.xlsx"
    assert result.tables[0].source_name == "客户旅程.xlsx"
    assert result.template.nodes[0].concept_id == "customer"
    assert retrieval_query("客户名称", "姓名") == "客户名称"


@pytest.mark.parametrize("score", [0.327, 0.93])
def test_candidates_are_drafts_not_per_item_confirmation(score):
    result = proposal(retriever=FakeRetriever(score=score))
    assert result.template.nodes[0].concept_id == "customer"
    assert not result.template.confirmed
    trace = result.tables[0].trace.model_copy(deep=True)
    adopted = validate_template(result.template, [source()], Catalog(catalog_bytes()))
    assert adopted.confirmed and not result.template.confirmed
    assert result.tables[0].trace == trace
    assert result.tables[0].confidence == score
    assert {p.column for p in adopted.nodes[0].properties} == {"id", "name"}


@pytest.mark.parametrize("status", ["unavailable", "mismatch"])
def test_ordinary_field_failure_does_not_exclude_valid_object(status):
    class FieldFailure(FakeRetriever):
        async def search_many(self, queries):
            values = await super().search_many(queries)
            return [
                value
                if value.query == "客户"
                else RetrievalResult(query=value.query, status=status, detail="字段检索失败")
                for value in values
            ]

    result = proposal(retriever=FieldFailure())
    assert result.tables[0].status == "mapped"
    assert result.tables[0].verification == "verified"
    assert len(result.template.nodes[0].properties) == 2
    assert all(c.concept_id is None for c in result.tables[0].columns)
    assert result.tables[0].trace.retrievals[1].status == status


@pytest.mark.parametrize(
    "retriever",
    [
        FakeRetriever(status="unavailable"),
        FakeRetriever(status="mismatch"),
        FakeRetriever(node_id=None),
        FakeRetriever(node_id="invented"),
    ],
)
def test_no_valid_entity_candidate_stays_excluded_with_evidence(retriever):
    result = proposal(retriever=retriever)
    assert result.template.nodes == []
    assert result.tables[0].reason


def test_valid_entity_group_does_not_depend_on_whole_table_match():
    model = FakeModel(
        query="未知表名",
        entities=[
            {
                "id": "client",
                "query": "客户",
                "key_columns": [],
                "properties": [{"column": c, "name": c} for c in ("id", "name")],
            }
        ],
    )
    result = proposal(model=model)
    assert result.tables[0].status == "unmatched"
    assert result.template.nodes[0].concept_id == "customer"
    assert len(result.template.nodes[0].properties) == 2


def test_unassigned_metadata_is_preserved_on_explicit_primary_group():
    model = FakeModel(
        entities=[
            {
                "id": "client",
                "query": "客户",
                "key_columns": [],
                "properties": [{"column": "name", "name": "name"}],
            }
        ]
    )
    result = proposal(model=model)
    assert {p.column for p in result.template.nodes[0].properties} == {"id", "name"}
    assert "1 个未分组字段" in "".join(result.tables[0].structure_notes)


def ready_run(store, result):
    run, seq, owner = store.create([source()])
    return store.update(run.id, seq, owner, finish=True, status="ready", result=result.model_dump())


def test_atomic_adoption_rolls_back_fork_if_another_job_is_active(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    result = proposal(retriever=FakeRetriever(score=0.327))
    old = ready_run(store, result)
    store.create([source("other")])
    adopted = result.model_copy(deep=True)
    adopted.template = validate_template(adopted.template, [source()], Catalog(catalog_bytes()))
    before = store.list()
    with pytest.raises(AlignmentError, match="已有"):
        store.start_graph(old.id, adopted)
    assert store.list() == before
    assert store.get(old.id) == old


def test_api_adopts_and_starts_once_without_template_save_or_individual_edits(
    tmp_path, monkeypatch
):
    path = tmp_path / "ontology.json"
    path.write_bytes(catalog_bytes())
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, ontology_snapshot=path))
    with TestClient(app) as client:
        service = app.state.alignment
        service.graph = SimpleNamespace(configured=True)
        started = []
        monkeypatch.setattr(service, "_spawn", lambda *args: started.append(args))
        original = ready_run(service.store, proposal(retriever=FakeRetriever(score=0.327)))
        original_template = original.result.template.model_dump()
        # Invalid proposals never create revisions or graph jobs.
        invalid = json.loads(json.dumps(original_template))
        invalid["nodes"][0]["concept_id"] = "invented"
        url = f"/api/v1/alignment/runs/{original.id}/graph"
        assert client.post(url, json=invalid).status_code == 422
        assert len(service.store.list()) == 1 and not started
        response = client.post(url, json=original_template)
        assert response.status_code == 202
        new = response.json()
        assert new["based_on_run_id"] == original.id
        assert new["graph_status"] == "building"
        assert new["result"]["template"]["confirmed"]
        assert new["result"]["tables"][0]["confidence"] == 0.327
        assert new["result"]["tables"][0]["status"] == "review"
        assert len(started) == 1 and started[0][0].id == new["id"]
        assert service.store.get(original.id) == original
        assert service.store.sources(new["id"]) == service.store.sources(original.id)
        with service.store.connect() as db:
            assert db.execute(
                "SELECT batch_id FROM run_batches WHERE run_id=?", (new["id"],)
            ).fetchall() == [("batch",)]
        # No orphan revision is left when a duplicate submit loses the job lease.
        assert client.post(url, json=original_template).status_code == 409
        assert len(service.store.list()) == 2


def test_graph_payload_supports_wide_templates_but_remains_bounded(tmp_path, monkeypatch):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    with TestClient(app) as client:
        run = ready_run(app.state.alignment.store, proposal())
        payload = run.result.template.model_dump()
        payload["nodes"][0]["properties"] = [
            {"column": "id", "name": f"property_{i}_" + "x" * 50} for i in range(1500)
        ]
        assert 64 * 1024 < len(json.dumps(payload)) < 1024 * 1024
        seen = []

        async def generate(run_id, template):
            seen.append(template)
            return run

        monkeypatch.setattr(app.state.alignment, "generate", generate)
        url = f"/api/v1/alignment/runs/{run.id}/graph"
        assert client.post(url, json=payload).status_code == 202
        assert len(seen[0].nodes[0].properties) == 1500
        assert client.post(url, content=b" " * (1024 * 1024 + 1)).status_code == 413
        assert len(seen) == 1
