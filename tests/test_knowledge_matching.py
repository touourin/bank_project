"""Matching may annotate ontology IDs; it may never rewrite source graph facts."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from test_alignment import FakeRetriever, catalog_bytes

from bank_project.alignment.models import AlignmentError
from bank_project.api.knowledge import AcceptMatchProposals, MatchDecision
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


def test_accept_low_score_proposals_atomically_retains_evidence_and_source(tmp_path):
    svc = service(tmp_path, score=0.177)
    value = finish(svc)
    assert value["confidence_threshold"] == 0.75
    accepted = svc.accept_proposals(
        value["id"],
        AcceptMatchProposals(expected_revision=1, reviewer="张三", note="已核对建议"),
    )
    assert accepted["revision"] == 2
    assert accepted["summary"]["matched_nodes"] == 2
    assert accepted["summary"]["matched_edges"] == 2
    assert all(node["reviewed"] for node in accepted["nodes"])
    assert all(edge["reviewed"] and edge["edge_type"] == "OWNS" for edge in accepted["edges"])
    assert [node["trace"] for node in accepted["nodes"]] == [
        node["trace"] for node in value["nodes"]
    ]
    assert len(accepted["audits"]) == 4
    assert all(
        audit["action"] == "accept_proposal"
        and audit["revision"] == 2
        and audit["reviewer"] == "张三"
        and audit["note"] == "已核对建议"
        for audit in accepted["audits"]
    )
    graph = svc.result_graph(value["id"])
    for original, annotated in zip(sample_graph()["nodes"], graph["nodes"], strict=True):
        assert annotated == {**original, "boid": "customer"}
    for original, annotated in zip(sample_graph()["edges"], graph["edges"], strict=True):
        assert annotated == {**original, "edge_type": "OWNS"}
    assert svc.store.get(value["id"])["graph"] == sample_graph()
    with pytest.raises(AlignmentError, match="刷新"):
        svc.accept_proposals(value["id"], AcceptMatchProposals(expected_revision=1))
    repeated = svc.accept_proposals(value["id"], AcceptMatchProposals(expected_revision=2))
    assert repeated["revision"] == 2 and repeated["audits"] == accepted["audits"]


@pytest.mark.parametrize("boid", [None, "account"])
def test_bulk_accept_keeps_manual_node_clear_and_override_in_legacy_runs(tmp_path, boid):
    svc = service(tmp_path, score=0.2)
    value = finish(svc)
    svc.review(
        value["id"], MatchDecision(target="node", target_id="a", boid=boid, expected_revision=1)
    )
    # Previously saved runs may have audit evidence without the explicit flag.
    svc.store.mutate(value["id"], lambda run: run["nodes"][0].pop("reviewed"))
    accepted = svc.accept_proposals(value["id"], AcceptMatchProposals(expected_revision=2))
    assert accepted["nodes"][0]["boid"] == boid
    assert accepted["nodes"][1]["boid"] == "customer"
    assert all(edge["edge_type"] is None for edge in accepted["edges"])
    assert len(accepted["audits"]) == 2


def test_bulk_accept_keeps_manually_cleared_edges_and_supports_legacy_suggestions(tmp_path):
    svc = service(tmp_path)
    value = finish(svc)
    svc.review(
        value["id"],
        MatchDecision(target="edge", target_id="e1", edge_type=None, expected_revision=1),
    )

    def legacy(run):
        for edge in run["edges"]:
            edge.pop("proposed_edge_type", None)
            edge.pop("reviewed", None)

    svc.store.mutate(value["id"], legacy)
    legacy_public = svc.store.public(svc.store.get(value["id"]))
    assert legacy_public["edges"][1]["proposed_edge_type"] == "OWNS"
    accepted = svc.accept_proposals(value["id"], AcceptMatchProposals(expected_revision=2))
    assert accepted["edges"][0]["edge_type"] is None
    assert accepted["edges"][1]["edge_type"] == "OWNS"
    assert accepted["revision"] == 3


def test_bulk_accept_rejects_catalog_change_without_partial_writes(tmp_path):
    svc = service(tmp_path, score=0.2)
    value = finish(svc)
    svc.settings.ontology_snapshot.write_bytes(catalog_bytes())
    with pytest.raises(AlignmentError, match="快照已变化"):
        svc.accept_proposals(value["id"], AcceptMatchProposals(expected_revision=1))
    assert svc.store.get(value["id"]) == value


@pytest.mark.parametrize("status", ["unavailable", "mismatch", "unmatched"])
def test_bulk_accept_never_uses_invalid_retrieval_evidence(tmp_path, status):
    svc = service(tmp_path, score=0.2)
    value = finish(svc)
    svc.store.mutate(value["id"], lambda run: run["nodes"][0]["trace"].update(status=status))
    accepted = svc.accept_proposals(value["id"], AcceptMatchProposals(expected_revision=1))
    assert accepted["nodes"][0]["boid"] is None
    assert accepted["nodes"][1]["boid"] == "customer"
    assert accepted["summary"]["matched_edges"] == 0


def test_bulk_accept_does_not_choose_ambiguous_edge_types(tmp_path):
    svc = service(tmp_path, score=0.2)
    snapshot = json.loads(svc.settings.ontology_snapshot.read_text())
    snapshot["graph"]["relationships"].append(
        {
            "start": "Concept:r1:customer",
            "end": "Concept:r1:customer",
            "type": "CONTROLS",
            "properties": {"dataset_revision": "r1"},
        }
    )
    svc.settings.ontology_snapshot.write_text(json.dumps(snapshot))
    value = finish(svc)
    accepted = svc.accept_proposals(value["id"], AcceptMatchProposals(expected_revision=1))
    assert accepted["edges"][0]["edge_type"] == "OWNS"
    assert accepted["edges"][1]["edge_type"] is None
    assert accepted["edges"][1]["proposed_edge_type"] is None
    assert accepted["edges"][1]["candidates"] == ["CONTROLS", "OWNS"]


def test_rematch_keeps_existing_annotations_when_new_retrieval_is_low_or_different(tmp_path):
    svc = service(tmp_path)
    first = finish(svc)

    async def execute():
        # New evidence points elsewhere; the selected graph's prior matching stays intact.
        svc.retriever.score, svc.retriever.node_id = 0.1, "account"
        second = await svc.start_match("graphrag", f"match:{first['id']}:1")
        await asyncio.gather(*svc.tasks)
        return svc.store.get(second["id"])

    second = asyncio.run(execute())
    output = svc.result_graph(second["id"])
    assert all(node["boid"] == "customer" for node in output["nodes"])
    assert output["edges"][0]["edge_type"] == "OWNS"
    assert all(node["trace"]["selected"]["id"] == "account" for node in second["nodes"])
    accepted = svc.accept_proposals(second["id"], AcceptMatchProposals(expected_revision=1))
    assert all(node["boid"] == "customer" for node in accepted["nodes"])
    assert svc.store.get(second["id"])["graph"]["nodes"] == output["nodes"]


def test_failed_later_batch_retains_completed_matching_evidence(tmp_path):
    svc = service(tmp_path)
    graph = sample_graph()
    graph["nodes"] = [{**graph["nodes"][0], "id": f"n{i}"} for i in range(101)]
    graph["edges"] = []
    svc.graphrag.graph = lambda _: copy.deepcopy(graph)
    original_search = svc.retriever.search_many
    calls = 0

    async def fail_second_batch(queries):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("retrieval interrupted")
        return await original_search(queries)

    svc.retriever.search_many = fail_second_batch
    value = finish(svc)
    assert value["status"] == "failed"
    assert len(value["nodes"]) == value["summary"]["matched_nodes"] == 100
    assert all(node["trace"]["selected"]["id"] == "customer" for node in value["nodes"])
    assert value["graph"] == graph
    with pytest.raises(AlignmentError):
        svc.accept_proposals(value["id"], AcceptMatchProposals(expected_revision=1))


def test_bulk_edge_proposals_respect_catalog_direction(tmp_path):
    svc = service(tmp_path, score=0.2)
    snapshot = json.loads(svc.settings.ontology_snapshot.read_text())
    snapshot["graph"]["relationships"][0]["end"] = "Concept:r1:account"
    svc.settings.ontology_snapshot.write_text(json.dumps(snapshot))
    graph = sample_graph()
    graph["nodes"][1]["boid"] = "account"
    graph["edges"].append({"id": "reverse", "source": "b", "target": "a", "properties": {}})
    svc.graphrag.graph = lambda _: copy.deepcopy(graph)
    value = finish(svc)
    accepted = svc.accept_proposals(value["id"], AcceptMatchProposals(expected_revision=1))
    assert [node["boid"] for node in accepted["nodes"]] == ["customer", "account"]
    assert [edge["edge_type"] for edge in accepted["edges"]] == ["OWNS", "OWNS", None]
    assert accepted["edges"][2]["candidates"] == []
