"""Behavioral boundaries: untrusted model output, joins, durable leases and publication failures."""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from bank_project.alignment.analyzer import Analyzer
from bank_project.alignment.catalog import Catalog
from bank_project.alignment.graph import VersionedGraph, graph_edges, graph_rows
from bank_project.alignment.models import (
    AlignmentError,
    ColumnMapping,
    MappingResult,
    RelationProposal,
    Run,
    Selection,
    SourceTable,
    TableMapping,
)
from bank_project.alignment.relations import map_relations
from bank_project.alignment.retrieval import RetrievalResult, RetrieveResponse
from bank_project.alignment.sources import StagedSources
from bank_project.alignment.store import RunStore
from bank_project.intake.models import (
    BatchInfo,
    Column,
    DataRow,
    ForeignKey,
    ParsedSource,
    ParsedTable,
    TableInfo,
)
from bank_project.intake.store import BatchStore
from bank_project.main import create_app
from bank_project.settings import Settings


def catalog_bytes(revisions=("r1",)):
    nodes = [
        {"label": "OntologyDataset", "properties": {"revision": r, "status": "ready"}}
        for r in revisions
    ]
    for rev in revisions:
        nodes += [
            {
                "label": "Concept",
                "key": f"Concept:{rev}:{cid}",
                "properties": {"node_id": cid, "node_name": name, "dataset_revision": rev},
            }
            for cid, name in [("customer", "客户"), ("account", "账户")]
        ]
    return json.dumps({"graph": {"nodes": nodes, "relationships": []}}).encode()


def source(table_id="customers", names=("id", "name"), values=None):
    values = [["001", "甲公司"], ["002", "乙公司"]] if values is None else values
    return SourceTable(
        batch=BatchInfo(
            id="batch",
            name="bank",
            source_kind="mysql",
            source="localhost:3306/bank",
            created_at="2026-09-19",
            table_count=1,
            row_count=len(values),
        ),
        table=TableInfo(
            id=table_id,
            name=table_id,
            row_count=len(values),
            columns=[Column(name=n) for n in names],
        ),
        rows=[DataRow(number=i + 2, values=row) for i, row in enumerate(values)],
    )


def mapped(s):
    return TableMapping(
        table_id=s.table.id,
        batch_id=s.batch.id,
        table_name=s.table.name,
        row_count=len(s.rows),
        concept_id="customer",
        concept_name="客户",
        status="mapped",
        columns=[
            ColumnMapping(column=c.name, property_key=f"field_{i:03d}")
            for i, c in enumerate(s.table.columns)
        ],
    )


class FakeModel:
    def __init__(self, **changes):
        self.changes = changes
        self.calls = []

    async def complete(self, system, user):
        self.calls.append((system, user))
        return {
            "meaning": "每行代表一位客户",
            "query": "客户",
            "columns": [
                {"column": "id", "query": "客户编号", "role": "primary_key", "semantic": "id"},
                {"column": "name", "query": "客户名称", "semantic": "name"},
            ],
            **self.changes,
        }


class FakeRetriever:
    def __init__(self, score=0.93, status="ok", confident=True, node_id="customer"):
        self.score, self.status, self.confident, self.node_id = score, status, confident, node_id
        self.queries = []

    async def check_revision(self, revision):
        pass

    @asynccontextmanager
    async def session(self, revision):
        self.revision = revision
        yield self

    async def search_many(self, queries):
        self.queries.extend(queries)
        results = []
        for query in queries:
            hit = self.node_id if query == "客户" else None
            results.append(
                RetrievalResult(
                    query=query,
                    status=self.status,
                    response=RetrieveResponse(
                        dataset_revision=self.revision,
                        node_id=hit,
                        confident=self.confident,
                        match_method="exact" if hit else "none",
                        candidates=[{"node_id": hit, "score": self.score}] if hit else [],
                    )
                    if self.status == "ok"
                    else None,
                    detail="测试接口状态" if self.status != "ok" else "",
                )
            )
        return results


async def ignore_progress(message):
    pass


def analyze(model=None, retriever=None, s=None):
    return asyncio.run(
        Analyzer(model or FakeModel(), retriever or FakeRetriever()).analyze(
            [s or source()], Catalog(catalog_bytes()), ignore_progress
        )
    )


def test_catalog_requires_explicit_revision_and_does_not_union_datasets():
    with pytest.raises(AlignmentError, match="多个"):
        Catalog(catalog_bytes(("a", "b")))
    catalog = Catalog(catalog_bytes(("a", "b")), "a")
    assert catalog.revision == "a" and len(catalog.names) == 2
    with pytest.raises(AlignmentError, match="不存在"):
        Catalog(catalog_bytes(), "missing")


def test_catalog_corruption_is_a_safe_user_error():
    with pytest.raises(AlignmentError, match="格式"):
        Catalog(b'{"graph":false}')


def test_interpretation_does_not_override_source_keys_and_matching_comes_from_retrieve():
    result = analyze()
    table = result.tables[0]
    assert table.status == "mapped" and table.verification == "verified"
    assert table.columns[0].role == "attribute"
    assert table.warnings
    s = source()
    s.table.columns[0].primary_key = True
    assert analyze(s=s).tables[0].columns[0].role == "primary_key"


@pytest.mark.parametrize(
    "changes",
    [
        {"concept_id": "invented"},
        {"confidence": 0.99},
        {"columns": [{"column": "id", "query": "编号"}]},
        {"columns": [{"column": "id", "query": "编号"}, {"column": "id", "query": "编号"}]},
        {"columns": [{"column": "id", "query": "编号"}, {"column": "other", "query": "名称"}]},
        {"query": " "},
        {"secret_extra": "not allowed"},
    ],
)
def test_invalid_interpretations_cannot_select_nodes_or_be_published(changes):
    retriever = FakeRetriever()
    assert analyze(FakeModel(**changes), retriever).tables[0].status == "failed"
    assert not retriever.queries


def test_retrieve_scores_determine_review_and_model_is_called_once_per_table():
    model = FakeModel()
    table = analyze(model, FakeRetriever(score=0.3)).tables[0]
    assert table.status == "review" and table.confidence == 0.3
    assert len(model.calls) == 1
    assert "本体候选目录" not in model.calls[0][1]
    assert analyze(retriever=FakeRetriever(confident=False)).tables[0].status == "review"
    assert analyze(retriever=FakeRetriever(node_id=None)).tables[0].status == "unmatched"


@pytest.mark.parametrize("state", ["mismatch", "unavailable"])
def test_failed_retrieval_blocks_auto_generation(state):
    table = analyze(retriever=FakeRetriever(status=state)).tables[0]
    assert table.status == "review" and table.verification == state
    assert table.concept_id is None and len(table.columns) == 2


def test_exact_composite_join_keeps_zeroes_nulls_and_candidate_provenance():
    left = source("left", ("tenant", "id"), [["a", "001"], ["a", "1"], ["a", None], ["b", "001"]])
    right = source("right", ("tenant", "id"), [["a", "001"], ["b", "001"]])
    proposal = RelationProposal(
        target_table_id="right",
        source_columns=["tenant", "id"],
        target_columns=["tenant", "id"],
        reason="候选关联",
    )
    mappings = [mapped(left), mapped(right)]
    relations = map_relations([left, right], mappings, {"left": [proposal]})
    assert relations[0].matched_rows == 2
    assert relations[0].origin == "candidate"
    result = MappingResult(revision="r1", snapshot_sha256="x", tables=mappings, relations=relations)
    edges = list(graph_edges([left, right], result))
    assert len(edges) == 2 and edges[0]["origin"] == "candidate"
    assert edges[1]["source"] == "left:3"


def test_ambiguous_target_never_chooses_first_match():
    left, right = source("left"), source("right", values=[["001", "甲"], ["001", "乙"]])
    p = RelationProposal(
        target_table_id="right", source_columns=["id"], target_columns=["id"], reason="id"
    )
    relation = map_relations([left, right], [mapped(left), mapped(right)], {"left": [p]})[0]
    assert relation.status == "skipped" and "不唯一" in relation.warnings[0]


def test_declared_fk_wins_and_other_database_is_not_matched():
    left, right = source("left"), source("right")
    fk = ForeignKey(
        name="fk", columns=["id"], target_columns=["id"], target_schema="bank", target_table="right"
    )
    left.table.foreign_keys = [fk]
    p = RelationProposal(
        target_table_id="right", source_columns=["id"], target_columns=["id"], reason="id"
    )
    result = map_relations([left, right], [mapped(left), mapped(right)], {"left": [p]})
    assert len(result) == 1 and result[0].origin == "declared"
    fk.target_schema = "other"
    result = map_relations([left, right], [mapped(left), mapped(right)], {})
    assert result[0].status == "skipped"


def test_node_identity_is_row_based_not_guessed_business_key_and_values_stay_exact():
    s = source(values=[["001", "12345678901234567890.1200"], ["001", ""], [None, None]])
    result = MappingResult(revision="r1", snapshot_sha256="x", tables=[mapped(s)], relations=[])
    rows = list(graph_rows([s], result))
    assert len({n["id"] for n in rows}) == 3
    assert rows[0]["properties"]["field_001"] == "12345678901234567890.1200"
    assert rows[1]["properties"]["field_001"] == ""
    assert json.loads(rows[2]["data"])["fields"]["name"] is None


def test_immutable_source_snapshot_survives_intake_deletion_and_caps_aggregate_size(tmp_path):
    batches = BatchStore(tmp_path / "batches.db")
    s = source()
    batch = batches.save(
        ParsedSource([ParsedTable("customer", s.table.columns, s.rows)]),
        name="example",
        source_kind="file",
        source="example.csv",
    )
    selection = Selection(batch_id=batch.id, table_id=batch.tables[0].id)
    with pytest.raises(AlignmentError, match="单元格"):
        StagedSources(batches, 1).read([selection])
    with pytest.raises(AlignmentError, match="100 MB"):
        StagedSources(batches, 100, max_bytes=1).read([selection])
    sources = StagedSources(batches, 100).read([selection])
    store = RunStore(tmp_path / "runs.db")
    run, _, _ = store.create(sources)
    batches.delete(batch.id)
    assert store.sources(run.id)[0].rows[0].values[0] == "001"


def test_job_lease_serializes_workers_and_stale_worker_cannot_finish(tmp_path):
    store, other = RunStore(tmp_path / "runs.db"), RunStore(tmp_path / "runs.db")
    run, sequence, owner = store.create([source()])
    with pytest.raises(AlignmentError, match="执行"):
        other.create([source()])
    with store.connect() as db:
        db.execute("UPDATE jobs SET expires=0")
    assert other.get(run.id).status == "failed"
    new, _, _ = other.create([source()])
    assert new.id != run.id
    with pytest.raises(AlignmentError, match="租约"):
        store.update(run.id, sequence, owner, finish=True, status="ready")


def test_graph_failure_never_runs_publish_pointer_query():
    s = source()
    run = Run(
        id="run",
        created_at="today",
        result=MappingResult(revision="r1", snapshot_sha256="x", tables=[mapped(s)], relations=[]),
    )
    graph = VersionedGraph(Settings(_env_file=None, neo4j_password="test"))
    queries = []
    from unittest.mock import MagicMock

    driver = MagicMock()

    def query(session, text, **params):
        queries.append(text)
        if "CREATE (n:BankAlignedInstance" in text:
            from neo4j.exceptions import ServiceUnavailable

            raise ServiceUnavailable("safe injected failure")
        return [{"ticket": 1}]

    with (
        patch.object(graph, "_driver", return_value=driver),
        patch.object(graph, "_query", side_effect=query),
    ):
        with pytest.raises(AlignmentError, match="未完成"):
            graph.publish(run, [s], 1, lambda: None)
    assert not any("s.version=$version" in q for q in queries)
    assert not any("DELETE" in q for q in queries)


def test_step_two_api_end_to_end_with_fake_model_and_persisted_run(tmp_path):
    path = tmp_path / "ontology.json"
    path.write_bytes(catalog_bytes())
    settings = Settings(
        _env_file=None, data_dir=tmp_path, ontology_snapshot=path, model_api_key="test"
    )
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.alignment.analyzer.model = FakeModel()
        app.state.alignment.analyzer.retriever = FakeRetriever()
        batch = client.post(
            "/api/v1/intake/uploads?filename=customer.csv", content=b"id,name\n001,Alpha\n002,Beta"
        ).json()
        payload = {"tables": [{"batch_id": batch["id"], "table_id": batch["tables"][0]["id"]}]}
        response = client.post("/api/v1/alignment/runs", json=payload)
        assert response.status_code == 202
        run_id = response.json()["id"]
        for _ in range(100):
            run = client.get(f"/api/v1/alignment/runs/{run_id}").json()
            if run["status"] != "analyzing":
                break
            time.sleep(0.01)
        assert run["status"] == "ready"
        assert run["result"]["tables"][0]["status"] == "mapped"
        assert client.post(f"/api/v1/alignment/runs/{run_id}/graph").status_code == 503
        assert (
            client.post(
                "/api/v1/alignment/runs", json={"tables": payload["tables"] * 2}
            ).status_code
            == 422
        )
        assert client.post("/api/v1/alignment/runs", content=b"x" * 65537).status_code == 413
        assert (
            client.get(
                "/api/v1/alignment/config", headers={"origin": "https://attacker.invalid"}
            ).status_code
            == 403
        )
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/v1/alignment/runs/{run_id}").json()["status"] == "ready"


def test_step_two_auth_and_config_do_not_expose_secrets(tmp_path):
    settings = Settings(
        _env_file=None, data_dir=tmp_path, api_token="x" * 24, model_api_key="private-key"
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/alignment/config").status_code == 401
        response = client.get(
            "/api/v1/alignment/config", headers={"Authorization": "Bearer " + "x" * 24}
        )
        assert response.status_code == 200 and "private-key" not in response.text
