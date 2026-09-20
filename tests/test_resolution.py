"""Full-graph evidence preservation, reversible review and conflict-safe concurrency."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import pytest

from bank_project.alignment.models import AlignmentError
from bank_project.resolution.adapter import corpus_from_graph, validate_graph
from bank_project.resolution.models import DecisionRequest, ManualRequest, StartRequest
from bank_project.resolution.service import ResolutionService
from bank_project.resolution.store import ResolutionStore
from bank_project.settings import Settings


def source(kind="graphrag", count=3):
    nodes = [
        {
            "id": "a",
            "name": "合成甲银行",
            "type": "organization",
            "boid": "BFO_A",
            "properties": {
                "title": "合成甲银行",
                "aliases": ["合成甲银"],
                "description": "合成甲银行位于上海",
                "fields": {"cust_id": "0001", "余额": "9007199254740993.01", "空值": None},
            },
        },
        {
            "id": "b",
            "name": "合成甲银",
            "type": "organization",
            "boid": "BFO_A",
            "properties": {
                "title": "合成甲银",
                "description": "合成甲银上海总部",
                "fields": {"cust_id": "0001", "余额": "2.00", "空串": ""},
                "nested": [None, {"x": "原值"}],
            },
        },
        {
            "id": "c",
            "name": "合成甲银行",
            "type": "organization",
            "properties": {"title": "合成甲银行", "fields": {"cust_id": "0002", "余额": "3.00"}},
        },
    ]
    for i in range(3, count):
        nodes.append(
            {
                "id": f"node-{i}",
                "name": f"独立节点 {i}",
                "type": "other",
                "properties": {"original": i},
            }
        )
    edges = [
        {
            "id": "e1",
            "source": "a",
            "target": "b",
            "properties": {
                "description": "两份来源记录之间的关系",
                "weight": 1,
                "text_unit_ids": ["t1"],
                "null": None,
            },
            "edge_type": "SAME_SOURCE",
        },
        {
            "id": "e2",
            "source": "a",
            "target": "c",
            "properties": {"description": "甲交易", "weight": 2},
        },
        {
            "id": "e3",
            "source": "b",
            "target": "c",
            "properties": {"description": "乙交易", "weight": 3},
        },
    ]
    if count > 3:
        edges.append(
            {
                "id": "last",
                "source": f"node-{count - 1}",
                "target": "c",
                "properties": {"description": "超过预览范围的关系"},
            }
        )
    return {
        "id": str(uuid4()),
        "name": "合成来源图",
        "source_kind": kind,
        "source_id": "source-version",
        "nodes": nodes,
        "edges": edges,
        "metadata": {"keep": ["source"]},
    }


def ready(tmp_path, graph=None, model=None):
    graph = graph or source()
    service = ResolutionService(
        Settings(_env_file=None, data_dir=tmp_path), lambda kind, key: deepcopy(graph)
    )
    if model:
        service.model = model

    async def run():
        started = await service.start(
            StartRequest(source_kind=graph["source_kind"], source_id=graph["source_id"])
        )
        await asyncio.gather(*service.tasks)
        result = service.get(started.id)
        assert result.status == "ready", result.error
        return result

    return service, asyncio.run(run())


def candidate(run, *ids):
    return next(row for row in run.candidates if set(row.node_ids) == set(ids))


def decide(service, run, ids, action="merge", canonical=None):
    return service.decide(
        run.id,
        DecisionRequest(
            candidate_id=candidate(run, *ids).id,
            action=action,
            canonical_id=canonical,
            expected_revision=run.revision,
            reviewer="合成审核员",
            note="核对来源",
        ),
    )


@pytest.mark.parametrize("kind", ["graphrag", "database"])
def test_complete_graph_merge_preserves_every_original_fact_and_undo(tmp_path, kind):
    original = source(kind, count=75)
    service, run = ready(tmp_path, original)
    assert run.summary.original_node_count == 75
    assert run.diagnostics["source_complete"] is True
    before = service.store.original(run.id)
    assert before == original
    assert run.summary.node_count == 75  # Even exact names are not merged without review.
    pair = candidate(run, "a", "b")
    assert any("别名" in reason for reason in pair.reasons)
    assert any(item.field == "余额" for item in pair.conflicts)
    run = decide(service, run, ("a", "b"), canonical="b")
    final = service.graph(run.id)
    assert final["id"] == run.id and final["source_id"] == original["source_id"]
    assert final["parent_graph"]["id"] == original["id"]
    assert len(final["nodes"]) == 74 and len(final["edges"]) == 4
    merged = next(node for node in final["nodes"] if node["id"] == "b")
    assert merged["properties"] == original["nodes"][1]["properties"]
    assert merged["resolution"]["source_nodes"] == original["nodes"][:2]
    assert (
        final["edges"][0]["source"] == final["edges"][0]["target"] == "b"
    )  # retain source self-edge
    assert (
        final["edges"][1]["source"] == final["edges"][2]["source"] == "b"
    )  # retain parallel source facts
    assert [edge["properties"] for edge in final["edges"]] == [
        edge["properties"] for edge in original["edges"]
    ]
    assert final["edges"][-1] == original["edges"][-1]  # beyond initial preview
    assert service.store.original(run.id) == original
    assert run.merges[0].target_node.id == "b"
    reopened = ResolutionStore(service.store.path)
    assert reopened.graph(run.id) == final
    run = decide(service, run, ("a", "b"), action="reset")
    restored = service.graph(run.id)
    assert restored["nodes"] == original["nodes"] and restored["edges"] == original["edges"]
    assert [audit.action for audit in run.audits] == ["merge", "reset"]


def test_cannot_link_protects_full_cluster_and_failed_decision_is_atomic(tmp_path):
    service, run = ready(tmp_path)
    run = decide(service, run, ("a", "c"), action="reject")
    run = decide(service, run, ("a", "b"))
    snapshot = service.get(run.id).model_dump()
    with pytest.raises(AlignmentError, match="冲突"):
        decide(service, run, ("b", "c"))
    assert service.get(run.id).model_dump() == snapshot
    assert len(service.graph(run.id)["nodes"]) == 2
    run = decide(service, run, ("a", "c"), action="reset")
    run = decide(service, run, ("b", "c"), canonical="c")
    assert run.summary.node_count == 1
    run = decide(service, run, ("a", "b"), action="reset")
    assert run.summary.node_count == 2
    assert {node["id"] for node in service.graph(run.id)["nodes"]} == {"a", "c"}


def test_concurrent_review_rejects_stale_revisions_and_is_durable(tmp_path):
    service, run = ready(tmp_path)
    payload = DecisionRequest(
        candidate_id=candidate(run, "a", "b").id, action="merge", expected_revision=0
    )

    def edit():
        try:
            return service.decide(run.id, payload)
        except AlignmentError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: edit(), range(2)))
    assert (
        sum(isinstance(value, AlignmentError) and value.status == 409 for value in responses) == 1
    )
    saved = service.get(run.id)
    assert saved.revision == 1 and len(saved.audits) == 1


def test_manual_review_can_add_unretrieved_pair_and_validates_canonical(tmp_path):
    graph = source(count=4)
    service, run = ready(tmp_path, graph)
    before = run.model_dump()
    with pytest.raises(AlignmentError, match="保留节点"):
        service.manual(
            run.id, ManualRequest(node_ids=["a", "node-3"], expected_revision=0, canonical_id="b")
        )
    assert service.get(run.id).model_dump() == before
    run = service.manual(
        run.id,
        ManualRequest(
            node_ids=["a", "node-3"],
            expected_revision=0,
            canonical_id="node-3",
            note="核对了原始来源",
        ),
    )
    assert run.summary.node_count == 3
    assert run.audits[0].action == "manual"
    assert candidate(run, "a", "node-3").evidence["origin"] == "manual"


@pytest.mark.parametrize("damage", ["preview", "count", "dangling", "duplicate", "source"])
def test_rejects_incomplete_or_ambiguous_source_graph(damage):
    graph = source()
    if damage == "preview":
        graph["edges_truncated"] = True
    elif damage == "count":
        graph["summary"] = {"node_count": 90}
    elif damage == "dangling":
        graph["edges"][0]["target"] = "missing"
    elif damage == "duplicate":
        graph["nodes"].append(deepcopy(graph["nodes"][0]))
    else:
        graph["source_id"] = "switched-version"
    with pytest.raises(AlignmentError):
        validate_graph(graph, "graphrag", "source-version")


def test_identifier_leading_zero_is_literal_and_never_assumed_verified():
    corpus = corpus_from_graph(source())
    assert corpus.mentions[0].identifiers[0].value == "0001"
    assert all(not value.verified for mention in corpus.mentions for value in mention.identifiers)


def test_model_proposals_have_grounded_quotes_and_never_auto_merge(tmp_path):
    class Model:
        configured = True

        async def complete(self, system, user):
            import json

            if "alternate names" in system:
                return {"aliases": []}
            pair = json.loads(user)
            assert "untrusted data" in system
            return {
                "verdict": "same",
                "left_quote": pair["left"]["context"],
                "right_quote": pair["right"]["context"],
                "reason": "合成原文说明别名",
            }

    service, run = ready(tmp_path, model=Model())
    assert run.summary.node_count == 3
    assert run.summary.merged_count == 0
    assert all(item.evidence["proposal"] == "same" for item in run.candidates)
    assert all(item.evidence["left_quote"] for item in run.candidates)
    assert service.store.original(run.id)["nodes"][0]["name"] == "合成甲银行"


def test_invalid_model_quotes_remain_reviewable_and_do_not_destroy_graph(tmp_path):
    class Model:
        configured = True

        async def complete(self, system, user):
            if "alternate names" in system:
                return {"aliases": []}
            return {
                "verdict": "same",
                "left_quote": "编造的外部事实",
                "right_quote": "编造的外部事实",
                "reason": "不可信",
            }

    service, run = ready(tmp_path, model=Model())
    assert run.summary.node_count == 3 and run.diagnostics["judge_failures"] == 3
    assert all(item.evidence["reason"] == "JUDGE_FAILED" for item in run.candidates)
    assert len(service.graph(run.id)["edges"]) == 3


def test_source_grounded_alias_expansion_retrieves_different_language_names(tmp_path):
    graph = source()
    graph["nodes"] = [
        {
            "id": "a",
            "name": "阿尔法",
            "type": "organization",
            "properties": {"description": "合成公司"},
            "source_context": "阿尔法（ALPHA）是一家合成公司。",
            "source_text_unit_ids": ["text-a"],
        },
        {
            "id": "b",
            "name": "ALPHA",
            "type": "organization",
            "properties": {"description": "测试公司"},
            "source_context": "ALPHA 是阿尔法的英文名。",
            "source_text_unit_ids": ["text-b"],
        },
    ]
    graph["edges"] = []

    class Model:
        configured = True

        async def complete(self, system, user):
            import json

            value = json.loads(user)
            if "alternate names" in system:
                return (
                    {"aliases": [{"alias": "ALPHA", "quote": "阿尔法（ALPHA）是一家合成公司。"}]}
                    if value["name"] == "阿尔法"
                    else {"aliases": []}
                )
            return {
                "verdict": "same",
                "left_quote": "阿尔法（ALPHA）是一家合成公司。",
                "right_quote": "ALPHA 是阿尔法的英文名。",
                "reason": "原文直接说明中英文名",
            }

    service, run = ready(tmp_path, graph, Model())
    pair = candidate(run, "a", "b")
    assert pair.score == 1 and pair.evidence["proposal"] == "same"
    assert pair.evidence["sources"][0]["text_unit_ids"] == ["text-a"]
    assert run.summary.node_count == 2
    assert service.store.original(run.id) == graph


def test_shutdown_cancels_model_calls_and_marks_run_failed_without_source_mutation(tmp_path):
    entered = asyncio.Event()
    cancelled = []

    class Model:
        configured = True

        async def complete(self, system, user):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)

    graph = source()
    service = ResolutionService(
        Settings(_env_file=None, data_dir=tmp_path), lambda kind, key: deepcopy(graph)
    )
    service.model = Model()

    async def run():
        started = await service.start(
            StartRequest(source_kind="graphrag", source_id="source-version")
        )
        await asyncio.wait_for(entered.wait(), 3)
        await service.close()
        return service.get(started.id)

    result = asyncio.run(run())
    assert result.status == "failed" and cancelled
    assert len(graph["nodes"]) == 3 and len(graph["edges"]) == 3


def test_expired_jobs_fail_and_no_partial_graph_is_exported(tmp_path):
    import time

    store = ResolutionStore(tmp_path / "runs.sqlite3")
    run, owner = store.create("database", "source")
    with store.connect() as db:
        db.execute("UPDATE resolution_runs SET expires=? WHERE id=?", (time.time() - 1, run.id))
    assert store.get(run.id).status == "failed"
    with pytest.raises(AlignmentError, match="准备完成"):
        store.graph(run.id)
    with pytest.raises(AlignmentError, match="租约"):
        store.heartbeat(run.id, owner)


def test_second_resolution_uses_previous_members_aliases_context_and_conflicts(tmp_path):
    graph = source()
    graph["nodes"] = [
        {
            "id": "a",
            "name": "AAAA",
            "type": "organization",
            "properties": {"description": "", "fields": {"amount": "1"}},
        },
        {
            "id": "b",
            "name": "BBBB",
            "type": "organization",
            "properties": {
                "description": "",
                "aliases": ["CCCC"],
                "fields": {"cust_id": "00042", "amount": "2"},
            },
            "source_context": "BBBB和CCCC是同一公司的两个名称。",
            "source_text_unit_ids": ["unit-b"],
        },
        {
            "id": "c",
            "name": "CCCC",
            "type": "organization",
            "properties": {"description": "", "fields": {"amount": "3"}},
        },
    ]
    graph["edges"] = []
    service, first = ready(tmp_path / "first", graph)
    assert not any(set(row.node_ids) == {"a", "c"} for row in first.candidates)
    first = service.manual(
        first.id, ManualRequest(node_ids=["a", "b"], canonical_id="a", expected_revision=0)
    )
    derivative = service.graph(first.id)
    corpus = corpus_from_graph(derivative)
    anchor = next(mention for mention in corpus.mentions if mention.mention_id == "a")
    assert {"BBBB", "CCCC"} <= set(anchor.aliases)
    assert "BBBB和CCCC是同一公司的两个名称。" in anchor.context
    assert anchor.identifiers[0].value == "00042"
    _, second = ready(tmp_path / "second", derivative)
    suggestion = candidate(second, "a", "c")
    assert suggestion.score == 1
    amounts = next(conflict for conflict in suggestion.conflicts if conflict.field == "amount")
    assert {row["value"] for row in amounts.values} == {"1", "2", "3"}
    canonical = next(node for node in derivative["nodes"] if node["id"] == "a")
    assert canonical["properties"] == graph["nodes"][0]["properties"]
    assert suggestion.evidence["sources"][0]["text_unit_ids"] == ["unit-b"]
