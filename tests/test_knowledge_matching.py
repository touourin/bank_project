"""Matching may annotate ontology IDs; it may never rewrite source graph facts."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from test_alignment import FakeRetriever, catalog_bytes

from bank_project.alignment.models import AlignmentError
from bank_project.api.knowledge import MatchDecision
from bank_project.knowledge.service import KnowledgeService
from bank_project.settings import Settings


def sample_graph():
    return {
        "id": "dataset",
        "name": "测试图谱",
        "source_kind": "graphrag",
        "source_id": "dataset",
        "nodes": [
            {
                "id": "a",
                "name": "甲公司",
                "type": "企业",
                "properties": {
                    "title": "甲公司",
                    "type": "ORGANIZATION",
                    "description": "客户",
                    "human_readable_id": 0,
                    "text_unit_ids": ["t1"],
                    "unknown": {"null": None, "code": "001"},
                },
            },
            {
                "id": "b",
                "name": "乙公司",
                "type": "企业",
                "properties": {"title": "乙公司", "description": "客户", "unknown": [1, False]},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "a",
                "target": "b",
                "properties": {
                    "source": "甲公司",
                    "target": "乙公司",
                    "type": "OWNS",
                    "weight": 1.25,
                    "description": "原始持股关系",
                    "text_unit_ids": ["t1", "t2"],
                },
            },
            {
                "id": "e2",
                "source": "a",
                "target": "b",
                "properties": {
                    "source": "甲公司",
                    "target": "乙公司",
                    "description": "另一个事实",
                    "weight": 3,
                },
            },
        ],
    }


def service(tmp_path, score=0.93):
    snapshot = json.loads(catalog_bytes())
    snapshot["graph"]["relationships"] = [
        {
            "start": "Concept:r1:customer",
            "end": "Concept:r1:customer",
            "type": "OWNS",
            "properties": {"dataset_revision": "r1"},
        }
    ]
    path = tmp_path / "ontology.json"
    path.write_text(json.dumps(snapshot))
    graph = sample_graph()
    provider = SimpleNamespace(
        graph=lambda key: copy.deepcopy(graph),
        list_datasets=lambda: [{"key": "dataset", "name": "测试图谱", "status": "succeeded"}],
    )
    svc = KnowledgeService(
        Settings(_env_file=None, data_dir=tmp_path, ontology_snapshot=path),
        provider,
        SimpleNamespace(configured=False),
    )

    class NodeRetriever(FakeRetriever):
        async def search_many(self, queries):
            results = await super().search_many(["客户"] * len(queries))
            self.queries.extend(queries)
            for result, query in zip(results, queries, strict=True):
                result.query = query
            return results

    svc.retriever = NodeRetriever(score=score)
    return svc


def finish(svc):
    async def execute():
        value = await svc.start_match("graphrag", "dataset")
        await asyncio.gather(*svc.tasks)
        return svc.store.get(value["id"])

    return asyncio.run(execute())


def test_matching_reuses_threshold_and_preserves_every_original_value(tmp_path):
    svc = service(tmp_path)
    value = finish(svc)
    assert value["status"] == "ready"
    assert value["summary"]["matched_nodes"] == 2
    assert value["summary"]["matched_edges"] == 1
    assert value["edges"][1]["edge_type"] is None  # no invented relation semantics
    output = svc.result_graph(value["id"])
    original = sample_graph()
    for before, after in zip(original["nodes"], output["nodes"], strict=True):
        assert after == {**before, "boid": "customer"}
    assert output["edges"][0] == {**original["edges"][0], "edge_type": "OWNS"}
    assert output["edges"][1] == original["edges"][1]
    assert svc.store.get(value["id"])["graph"] == original
    assert all(n["trace"]["query"] in svc.retriever.queries for n in value["nodes"])


def test_low_confidence_stays_unannotated_until_review_with_audit_and_cas(tmp_path):
    svc = service(tmp_path, score=0.2)
    value = finish(svc)
    assert value["summary"]["matched_nodes"] == 0
    assert all(n["trace"]["status"] == "review" for n in value["nodes"])
    assert svc.result_graph(value["id"])["nodes"] == sample_graph()["nodes"]
    payload = MatchDecision(
        target="node",
        target_id="a",
        boid="customer",
        expected_revision=1,
        reviewer="张三",
        note="已核对原文",
    )
    reviewed = svc.review(value["id"], payload)
    assert reviewed["revision"] == 2
    assert reviewed["nodes"][0]["trace"] == value["nodes"][0]["trace"]
    assert reviewed["audits"][0]["reviewer"] == "张三"
    with pytest.raises(AlignmentError, match="刷新"):
        svc.review(value["id"], payload)
    with pytest.raises(AlignmentError, match="BOID"):
        svc.review(
            value["id"], payload.model_copy(update={"boid": "invented", "expected_revision": 2})
        )
    assert svc.store.get(value["id"])["revision"] == 2


def test_changing_node_revalidates_incident_edges_and_catalog_is_pinned(tmp_path):
    svc = service(tmp_path)
    value = finish(svc)
    reviewed = svc.review(
        value["id"],
        MatchDecision(target="node", target_id="a", boid="account", expected_revision=1),
    )
    assert all(edge["edge_type"] is None for edge in reviewed["edges"])
    assert reviewed["nodes"][1]["boid"] == "customer"
    with pytest.raises(AlignmentError, match="方向约束"):
        svc.review(
            value["id"],
            MatchDecision(target="edge", target_id="e1", edge_type="OWNS", expected_revision=2),
        )
    svc.settings.ontology_snapshot.write_bytes(catalog_bytes())
    with pytest.raises(AlignmentError, match="快照已变化"):
        svc.concepts(value["id"], "客户")


def test_no_incomplete_graph_is_accepted(tmp_path):
    svc = service(tmp_path)
    graph = sample_graph()
    graph["nodes"].pop()
    svc.graphrag.graph = lambda key: graph
    with pytest.raises(AlignmentError, match="缺失端点"):
        svc.load_graph("graphrag", "dataset")


def test_manual_annotation_requires_explicit_target_value():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MatchDecision(target="node", target_id="a", expected_revision=1)
    assert MatchDecision(target="node", target_id="a", boid=None, expected_revision=1).boid is None
