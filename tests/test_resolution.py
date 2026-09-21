"""Full-graph evidence preservation, reversible review and conflict-safe concurrency."""

import asyncio
import json
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
    if kind == "graphrag":
        for node in nodes:
            node["source_context"] = f"合成测试原文：{node['name']}。" + json.dumps(
                node["properties"], ensure_ascii=False, sort_keys=True
            )
            node["source_text_unit_ids"] = [f"unit-{node['id']}"]
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


def ready(tmp_path, graph=None, model=None, options=None):
    graph = graph or source()
    service = ResolutionService(
        Settings(_env_file=None, data_dir=tmp_path), lambda kind, key: deepcopy(graph)
    )
    if model:
        service.model = model

    async def run():
        started = await service.start(
            StartRequest(
                source_kind=graph["source_kind"],
                source_id=graph["source_id"],
                options=options or {},
            )
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
@pytest.mark.parametrize("policy", ["balanced", "original"])
def test_identity_conflicts_and_missing_scope_block_candidate_and_manual_merges(
    tmp_path, kind, policy
):
    graph = source(kind)
    for record, name in zip(
        graph["nodes"], ["甲公司2025年年报", "甲公司年报", "甲公司2026年年报"], strict=True
    ):
        record.update(name=name, type="财务指标", properties={})
    service, run = ready(tmp_path, graph, options={"retrieval_policy": policy})
    if policy == "original":
        assert candidate(run, "a", "c").status == "excluded"
        with pytest.raises(AlignmentError, match="身份字段冲突"):
            decide(service, run, ("a", "c"))
        for action in ("reject", "reset"):
            with pytest.raises(AlignmentError, match="无需人工审核"):
                decide(service, run, ("a", "c"), action=action)
    else:
        assert all(set(row.node_ids) != {"a", "c"} for row in run.candidates)
        assert run.diagnostics["retrieval"]["identity_filtered_pairs"] == 1
    assert run.summary.pending_count == 0
    assert run.summary.excluded_count == (3 if policy == "original" else 2)
    assert not run.audits and run.revision == 0
    with pytest.raises(AlignmentError, match="身份字段冲突"):
        service.manual(run.id, ManualRequest(node_ids=["a", "c"], expected_revision=0))
    # A missing year also keeps records separate; no manual or transitive bridge.
    for ids in (("a", "b"), ("b", "c")):
        for action in ("merge", "reset", "reject"):
            with pytest.raises(AlignmentError, match="缺少证明为同一实体的依据"):
                decide(service, run, ids, action=action)
        with pytest.raises(AlignmentError, match="缺少证明为同一实体的依据"):
            service.manual(run.id, ManualRequest(node_ids=list(ids), expected_revision=0))
    assert service.get(run.id).revision == 0
    assert service.store.original(run.id) == graph
    assert run.summary.node_count == 3


def test_historical_conflicts_leave_review_queue_without_rewriting_original_decisions(tmp_path):
    graph = source()
    for record, name in zip(
        graph["nodes"], ["甲公司2025年年报", "甲公司年报", "甲公司2026年年报"], strict=True
    ):
        record.update(name=name, type="财务指标", properties={})
    # Historical/wide-recall records include the explicitly conflicting pair.
    service, run = ready(tmp_path, graph, options={"retrieval_policy": "original"})
    stored = run.model_dump()
    for row in stored["candidates"]:
        row.update(status="pending", evidence={"proposal": "same", "verdict": "uncertain"})
    stored["summary"].update(pending_count=3, excluded_count=0)
    raw = json.dumps(stored, ensure_ascii=False)
    with service.store.connect() as db:
        db.execute("UPDATE resolution_runs SET metadata=? WHERE id=?", (raw, run.id))
    current = service.get(run.id)
    assert current.summary.excluded_count == 3 and current.summary.pending_count == 0
    assert "3 组自动不合并" in current.progress
    assert candidate(current, "a", "c").evidence["proposal"] == "same"
    assert candidate(current, "a", "b").status == "excluded"
    listed = service.list()[0]
    assert listed.summary == current.summary
    assert current.revision == 0 and current.audits == []
    assert service.graph(run.id)["nodes"] == graph["nodes"]
    with service.store.connect() as db:
        assert (
            db.execute("SELECT metadata FROM resolution_runs WHERE id=?", (run.id,)).fetchone()[0]
            == raw
        )


@pytest.mark.parametrize("kind", ["graphrag", "database"])
def test_missing_scope_in_imported_merge_history_cannot_be_hidden_by_canonical_name(tmp_path, kind):
    graph = source(kind)
    for row in graph["nodes"][:2]:
        row.update(name="甲公司2025年年报", type="财务指标", properties={})
    graph["nodes"][1]["resolution"] = {
        "source_nodes": [
            {"id": "old-record", "name": "甲公司年报", "type": "财务指标", "properties": {}}
        ]
    }
    service, run = ready(tmp_path, graph)
    assert candidate(run, "a", "b").status == "not_recommended"
    with pytest.raises(AlignmentError, match="缺少证明为同一实体的依据"):
        service.manual(run.id, ManualRequest(node_ids=["a", "b"], expected_revision=0))
    assert service.get(run.id).revision == 0
    assert service.graph(run.id)["nodes"] == graph["nodes"]


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


@pytest.mark.parametrize("action", ["merge", "reset"])
def test_concurrent_review_rejects_stale_revisions_and_is_durable(tmp_path, action):
    service, run = ready(tmp_path)
    if action == "reset":
        run = decide(service, run, ("a", "b"))
    payload = DecisionRequest(
        candidate_id=candidate(run, "a", "b").id,
        action=action,
        expected_revision=run.revision,
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
    assert saved.revision == run.revision + 1 and len(saved.audits) == len(run.audits) + 1
    assert saved.audits[-1].action == action
    assert ResolutionStore(service.store.path).get(run.id) == saved
    if action == "reset":
        assert service.graph(run.id)["nodes"] == source()["nodes"]


@pytest.mark.parametrize("kind", ["graphrag", "database"])
def test_manual_merge_reset_restores_source_and_can_be_merged_again(tmp_path, kind):
    graph = source(kind, count=4)
    service, run = ready(tmp_path, graph)
    run = service.manual(
        run.id,
        ManualRequest(
            node_ids=["a", "node-3"], expected_revision=run.revision, canonical_id="node-3"
        ),
    )
    first_audit = run.audits[0].model_dump()
    run = service.decide(
        run.id,
        DecisionRequest(
            candidate_id=candidate(run, "a", "node-3").id,
            action="reset",
            expected_revision=run.revision,
            reviewer="复核员",
            note="来源并非同一主体，退回核验",
        ),
    )
    restored = service.graph(run.id)
    assert restored["nodes"] == graph["nodes"] and restored["edges"] == graph["edges"]
    assert service.store.original(run.id) == graph
    assert run.summary.merged_count == 0 and run.merges == []
    assert candidate(run, "a", "node-3").status == "not_recommended"
    assert candidate(run, "a", "node-3").canonical_id is None
    assert run.audits[0].model_dump() == first_audit
    audit = run.audits[-1]
    assert (audit.action, audit.previous_status, audit.reviewer, audit.revision) == (
        "reset",
        "merged",
        "复核员",
        2,
    )
    assert audit.note == "来源并非同一主体，退回核验"
    assert {node.id for node in audit.source_nodes} == {"a", "node-3"}
    assert service.get(run.id) == run

    run = service.manual(
        run.id,
        ManualRequest(node_ids=["a", "node-3"], expected_revision=run.revision, canonical_id="a"),
    )
    assert run.summary.node_count == 3 and run.summary.merged_count == 1
    assert run.merges[0].target_node.id == "a"
    assert [audit.action for audit in run.audits] == ["manual", "reset", "manual"]
    assert [audit.revision for audit in run.audits] == [1, 2, 3]
    assert service.store.original(run.id) == graph


def test_reset_removes_only_selected_merge_and_keeps_other_accepted_paths(tmp_path):
    graph = source()
    service, run = ready(tmp_path, graph)
    run = decide(service, run, ("a", "b"), canonical="b")
    run = decide(service, run, ("b", "c"), canonical="c")
    run = decide(service, run, ("a", "c"), canonical="a")
    run = decide(service, run, ("a", "b"), action="reset")
    assert run.summary.merged_count == 2 and run.summary.node_count == 1
    assert service.graph(run.id)["resolution"]["memberships"] == dict.fromkeys("abc", "a")
    assert {node.id for node in run.audits[-1].source_nodes} == {"a", "b", "c"}

    run = decide(service, run, ("a", "c"), action="reset")
    projected = service.graph(run.id)
    assert projected["resolution"]["memberships"] == {"a": "a", "b": "c", "c": "c"}
    assert [(edge["source"], edge["target"]) for edge in projected["edges"]] == [
        ("a", "c"),
        ("a", "c"),
        ("c", "c"),
    ]
    assert run.merges[0].target_node.id == "c"
    assert {node.id for node in run.merges[0].source_nodes} == {"b", "c"}

    run = decide(service, run, ("b", "c"), action="reset")
    restored = service.graph(run.id)
    assert restored["nodes"] == graph["nodes"] and restored["edges"] == graph["edges"]
    assert run.summary.merged_count == 0 and run.summary.node_count == 3
    assert [audit.action for audit in run.audits] == ["merge"] * 3 + ["reset"] * 3


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
    assert run.summary.pending_count == 3 and run.summary.not_recommended_count == 0
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
    assert run.summary.pending_count == 0 and run.summary.not_recommended_count == 3
    assert all(item.status == "not_recommended" for item in run.candidates)
    assert len(service.graph(run.id)["edges"]) == 3


def test_only_grounded_model_same_results_enter_confirmation_queue(tmp_path):
    calls = []

    class Model:
        configured = True

        async def complete(self, system, user):
            pair = json.loads(user)
            ids = frozenset(pair[side]["mention_id"] for side in ("left", "right"))
            calls.append(ids)
            verdict = {frozenset(("a", "b")): "same", frozenset(("a", "c")): "different"}.get(
                ids, "uncertain"
            )
            return {
                "verdict": verdict,
                "reason": "按合成原文证据判定",
                "left_quote": pair["left"]["context"],
                "right_quote": pair["right"]["context"],
            }

    service, run = ready(tmp_path, model=Model())
    assert len(calls) == 3
    assert candidate(run, "a", "b").status == "pending"
    assert candidate(run, "a", "c").status == "not_recommended"
    assert candidate(run, "b", "c").status == "not_recommended"
    assert run.summary.pending_count == 1 and run.summary.not_recommended_count == 2
    assert service.list()[0].summary == run.summary
    assert run.summary.merged_count == 0 and run.summary.node_count == 3
    assert "1 组模型合并建议待确认" in run.progress
    approved = decide(service, run, ("a", "b"))
    assert approved.summary.pending_count == 0
    undone = decide(service, approved, ("a", "b"), action="reset")
    assert undone.summary.pending_count == 1 and undone.summary.node_count == 3


def test_automatically_applied_merge_can_be_reset_and_confirmed_again(tmp_path):
    class Model:
        configured = True

        async def complete(self, system, user):
            pair = json.loads(user)
            ids = {pair[side]["mention_id"] for side in ("left", "right")}
            return {
                "verdict": "same" if ids == {"a", "b"} else "different",
                "reason": "按合成原文证据判定",
                "left_quote": pair["left"]["context"],
                "right_quote": pair["right"]["context"],
            }

    graph = source()
    service, run = ready(tmp_path, graph, model=Model(), options={"model_policy": "apply"})
    assert run.summary.merged_count == 1 and run.summary.node_count == 2
    assert candidate(run, "a", "b").status == "merged"
    assert run.audits[0].reviewer == "消歧引擎 · 自动应用"
    first_audit = run.audits[0].model_dump()

    run = decide(service, run, ("a", "b"), action="reset")
    assert candidate(run, "a", "b").status == "pending"
    assert run.summary.pending_count == 1 and run.summary.merged_count == 0
    assert run.audits[0].model_dump() == first_audit
    assert run.audits[-1].previous_status == "merged" and run.audits[-1].action == "reset"
    restored = service.graph(run.id)
    assert restored["nodes"] == graph["nodes"] and restored["edges"] == graph["edges"]
    assert service.get(run.id) == run

    run = decide(service, run, ("a", "b"), canonical="b")
    assert run.summary.merged_count == 1 and run.summary.pending_count == 0
    assert run.merges[0].target_node.id == "b"
    assert [audit.action for audit in run.audits] == ["merge", "reset", "merge"]
    assert [audit.revision for audit in run.audits] == [1, 2, 3]
    assert service.store.original(run.id) == graph


def test_without_model_does_not_present_name_similarity_as_merge_suggestions(tmp_path):
    service, run = ready(tmp_path)
    assert run.summary.pending_count == 0
    assert run.summary.not_recommended_count == len(run.candidates)
    assert any("尚未配置聊天模型" in w for w in run.diagnostics["warnings"])
    assert service.graph(run.id)["nodes"] == service.store.original(run.id)["nodes"]


def test_graphrag_generated_title_cannot_validate_a_document_quote(tmp_path):
    graph = source()
    graph["nodes"][0]["source_context"] = "BIS于2019年发布规则。"
    # The extracted title is intentionally absent from the real text.
    calls = []

    class Model:
        configured = True

        async def complete(self, system, user):
            pair = json.loads(user)
            calls.append(pair)
            return {
                "verdict": "same",
                "reason": "错误引用了抽取后的名称",
                "left_quote": pair["left"]["name"],
                "right_quote": pair["right"]["name"],
            }

    service, run = ready(tmp_path, graph, Model())
    row = candidate(run, "a", "b")
    assert row.status == "not_recommended"
    assert row.evidence["error_code"] == "UNSUPPORTED_SOURCE_QUOTE"
    assert all(
        record["context"] == "BIS于2019年发布规则。"
        for pair in calls
        for record in pair.values()
        if record["mention_id"] == "a"
    )
    assert service.graph(run.id)["nodes"] == graph["nodes"]


def test_historical_generated_quote_is_relabelled_and_cannot_be_accepted(tmp_path):
    graph = source()
    graph["nodes"][0]["source_context"] = "BIS于2019年发布规则。"
    service, run = ready(tmp_path, graph)
    row = candidate(run, "a", "b")
    row.status = "pending"
    row.evidence = {
        "origin": "model",
        "left": "a",
        "right": "b",
        "proposal": "same",
        "verdict": "uncertain",
        "left_quote": graph["nodes"][0]["name"],
        "right_quote": graph["nodes"][1]["name"],
        "model_reason": "历史模型判断",
    }
    raw = run.model_dump_json()
    with service.store.connect() as db:
        db.execute("UPDATE resolution_runs SET metadata=? WHERE id=?", (raw, run.id))
    current = service.get(run.id)
    reviewed = candidate(current, "a", "b")
    proof = reviewed.evidence["quote_validation"]
    assert reviewed.status == "not_recommended" and proof["supported"] is False
    assert proof["left"]["origin"] == "node_attribute" and "title" in proof["left"]["fields"]
    assert proof["right"]["origin"] == "source_text"
    assert reviewed.evidence["model_reason"] == "历史模型判断"
    assert service.list()[0].summary.pending_count == 0
    with pytest.raises(AlignmentError, match="来源校验"):
        decide(service, current, ("a", "b"))
    with service.store.connect() as db:
        assert (
            db.execute("SELECT metadata FROM resolution_runs WHERE id=?", (run.id,)).fetchone()[0]
            == raw
        )
    assert service.graph(run.id)["nodes"] == graph["nodes"]
    assert current.revision == 0 and not current.audits


def test_missing_document_text_is_not_replaced_by_generated_properties(tmp_path):
    graph = source()
    for node in graph["nodes"]:
        node.pop("source_context")

    class Model:
        configured = True

        async def complete(self, system, user):
            raise AssertionError("不能用生成属性替代缺失原文调用模型")

    _, run = ready(tmp_path, graph, Model())
    assert run.summary.pending_count == 0
    assert all(c.status == "not_recommended" for c in run.candidates)
    assert all(c.evidence["reason"] == "缺少文档原文，未生成合并建议" for c in run.candidates)


def test_database_quotes_are_labelled_as_source_fields(tmp_path):
    class Model:
        configured = True

        async def complete(self, system, user):
            pair = json.loads(user)
            return {
                "verdict": "same",
                "reason": "合成记录依据",
                "left_quote": pair["left"]["context"],
                "right_quote": pair["right"]["context"],
            }

    _, run = ready(tmp_path, source("database"), Model())
    proof = candidate(run, "a", "b").evidence["quote_validation"]
    assert proof["supported"] is True
    assert proof["left"]["origin"] == proof["right"]["origin"] == "source_record"


@pytest.mark.parametrize("kind", ["graphrag", "database"])
def test_candidate_sources_return_full_frozen_evidence_with_separate_description(tmp_path, kind):
    graph = source(kind)
    if kind == "graphrag":
        graph["nodes"][0]["source_context"] = "原文开头\n" + "BIS发布规则。" * 300 + "\n原文末尾"
    service, run = ready(tmp_path, graph)
    pair = candidate(run, "a", "b")
    pair.evidence.update(origin="model", left="a", right="b", left_quote=graph["nodes"][0]["name"])
    raw = run.model_dump_json()
    with service.store.connect() as db:
        db.execute("UPDATE resolution_runs SET metadata=? WHERE id=?", (raw, run.id))
    service.graph_loader = lambda *_: pytest.fail("历史依据不能读取当前索引")
    result = service.store.candidate_sources(run.id, pair.id)
    assert result["source_name"] == graph["name"] and result["source_kind"] == kind
    assert [n["node_id"] for n in result["nodes"]] == pair.node_ids
    for node in result["nodes"]:
        original = next(n for n in graph["nodes"] if n["id"] == node["node_id"])
        record = node["records"][0]
        assert record["description"] == original["properties"]["description"]
        if kind == "graphrag":
            assert record["source_text"] == original["source_context"]
            assert record["text_unit_ids"] == original["source_text_unit_ids"]
            assert record["fields"] is None
        else:
            assert record["source_text"] is None
            assert record["fields"] == original["properties"]
    left = next(n for n in result["nodes"] if n["node_id"] == "a")
    assert left["quote_location"]["origin"] == (
        "node_attribute" if kind == "graphrag" else "source_record"
    )
    with service.store.connect() as db:
        assert (
            db.execute("SELECT metadata FROM resolution_runs WHERE id=?", (run.id,)).fetchone()[0]
            == raw
        )


def test_candidate_sources_do_not_invent_missing_source_text(tmp_path):
    graph = source()
    graph["nodes"][0].pop("source_context")
    service, run = ready(tmp_path, graph)
    pair = candidate(run, "a", "b")
    result = service.store.candidate_sources(run.id, pair.id)
    left = next(n for n in result["nodes"] if n["node_id"] == "a")
    assert left["records"][0]["source_text"] is None
    assert left["records"][0]["description"]
    started, _ = service.store.create("graphrag", "pending-source")
    with pytest.raises(AlignmentError, match="分析完成"):
        service.store.candidate_sources(started.id, pair.id)


def test_analysis_reports_completed_items_while_other_model_requests_are_pending(tmp_path):
    import json

    async def exercise():
        release_aliases, release_pairs = asyncio.Event(), asyncio.Event()

        class Model:
            configured = True

            async def complete(self, system, user):
                payload = json.loads(user)
                if "alternate names" in system:
                    if payload["mention_id"] != "a":
                        await release_aliases.wait()
                    return {"aliases": []}
                pair = {payload[side]["mention_id"] for side in ("left", "right")}
                if pair != {"a", "b"}:
                    await release_pairs.wait()
                return {"verdict": "uncertain", "reason": "需要人工核对原文"}

        service = ResolutionService(
            Settings(_env_file=None, data_dir=tmp_path), lambda *args: source()
        )
        service.model = Model()
        started = await service.start(
            StartRequest(
                source_kind="graphrag",
                source_id="source-version",
                options={"method": "synonym_llm_v1"},
            )
        )

        async def wait_progress(text):
            while True:
                run = await asyncio.to_thread(service.get, started.id)
                assert run.status == "analyzing", run.error
                if text in run.progress:
                    return run
                await asyncio.sleep(0.01)

        try:
            aliases = await asyncio.wait_for(wait_progress("别名检查 1/3"), 3)
            assert "已用时" in aliases.progress
            assert aliases.candidates == []
            release_aliases.set()
            pairs = await asyncio.wait_for(wait_progress("候选比较 1/3"), 3)
            assert "失败 0，预算跳过 0" in pairs.progress
            assert len(pairs.candidates) == 1
            assert pairs.diagnostics["analysis"]["partial"] is True
            assert pairs.summary.original_node_count == 3
            with pytest.raises(AlignmentError, match="等待候选分析完成"):
                service.manual(started.id, ManualRequest(node_ids=["a", "b"], expected_revision=0))
            release_pairs.set()
            await asyncio.gather(*service.tasks)
            finished = service.get(started.id)
            assert finished.status == "ready" and len(finished.candidates) == 3
            assert finished.summary.node_count == 3
        finally:
            release_aliases.set()
            release_pairs.set()
            await service.close()

    asyncio.run(exercise())


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

    service, run = ready(tmp_path, graph, Model(), options={"method": "synonym_llm_v1"})
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
    assert not anchor.identifiers  # 抽取字段中的编号不在原文里，不能充当原文身份凭据。
    _, second = ready(tmp_path / "second", derivative)
    suggestion = candidate(second, "a", "c")
    assert suggestion.score == 1
    amounts = next(conflict for conflict in suggestion.conflicts if conflict.field == "amount")
    assert {row["value"] for row in amounts.values} == {"1", "2", "3"}
    canonical = next(node for node in derivative["nodes"] if node["id"] == "a")
    assert canonical["properties"] == graph["nodes"][0]["properties"]
    assert suggestion.evidence["sources"][0]["text_unit_ids"] == ["unit-b"]
