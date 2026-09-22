"""Regression coverage for table event identity, including the reported graph scale."""

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest

from bank_project.resolution.adapter import corpus_from_graph
from bank_project.resolution.engine.candidates import retrieve
from bank_project.resolution.engine.contracts import (
    CandidateBudgetExceeded,
    Constraint,
    Corpus,
    ResolverConfig,
    digest,
)
from bank_project.resolution.engine.model_judge import judgment_record
from bank_project.resolution.engine.resolver import resolve_evidence
from bank_project.resolution.models import StartRequest
from bank_project.resolution.service import ResolutionService
from bank_project.settings import Settings


def record(mid, row, fields, kind="业务事件信息", name=None):
    return {
        "id": mid,
        "name": name or f"{kind} · 第 {row} 行",
        "type": kind,
        "properties": {
            "concept_name": kind,
            "source_row": row,
            "table_id": "table",
            "fields": fields,
        },
    }


def event(mid, row, customer="0001", kind="开户", date="20260920", **extra):
    return record(
        mid,
        row,
        {
            "CUST_ID": customer,
            "事件类型": kind,
            "发生日期": date,
            "ROWKEY": mid,
            **extra,
        },
    )


def graph(*nodes):
    return {
        "id": "version",
        "source_id": "version",
        "source_kind": "database",
        "nodes": list(nodes),
        "edges": [],
    }


def pairs(corpus, policy="balanced", **options):
    candidates, _ = retrieve(corpus, ResolverConfig(retrieval_policy=policy, **options))
    return {tuple(sorted((a, b))) for a, others in candidates.items() for b in others}


@pytest.mark.parametrize("policy", ["original", "balanced"])
def test_same_participant_and_generated_labels_do_not_identify_events(policy):
    source = graph(event("a", 1), event("b", 2, kind="转账"), event("c", 3, date="20260921"))
    before = deepcopy(source)
    corpus = corpus_from_graph(source)
    assert pairs(corpus, policy) == set()
    assert all(not m.identifiers for m in corpus.mentions)
    assert all('"CUST_ID": "0001"' in m.context for m in corpus.mentions)
    assert source == before


@pytest.mark.parametrize("policy", ["original", "balanced"])
def test_possible_duplicate_events_survive_renamed_fields_and_different_rowkeys(policy):
    corpus = corpus_from_graph(
        graph(
            event("a", 1),
            record(
                "b",
                97,
                {"cust_id": "0001", "EVT_TYPE": "开户", "OCCUR_DT": "20260920", "ROWKEY": "b"},
            ),
            event("c", 3, customer="1"),
        )
    )
    assert pairs(corpus, policy) == {("a", "b")}
    assert Corpus.from_dict(corpus.to_dict()) == corpus
    assert "recall" not in judgment_record(corpus.mentions[0], "L")


def test_matching_event_blocks_are_never_verified_identity_or_automatic_merges():
    corpus = corpus_from_graph(graph(event("a", 1), event("b", 2)))
    result = asyncio.run(resolve_evidence(corpus, ResolverConfig(model_policy="apply")))
    assert len(result.entities) == 2
    assert result.diagnostics["trusted_identifier_assertions"] == 0
    assert result.decisions[0]["verdict"] == "uncertain"


def test_event_id_and_reviewed_constraints_still_recall_without_complete_dimensions():
    corpus = corpus_from_graph(
        graph(
            record("a", 1, {"event_id": "0007", "CUST_ID": "0001"}),
            record("b", 2, {"event_id": "0007", "CUST_ID": "0002"}),
            record("c", 3, {}),
        )
    )
    assert pairs(corpus) == {("a", "b")}
    assert {i.namespace for m in corpus.mentions for i in m.identifiers} == {"event_id"}
    forced = replace(corpus, constraints=(Constraint("b", "c", "different", "reviewed"),))
    assert pairs(forced) == {("a", "b"), ("b", "c")}


def test_missing_fields_do_not_use_display_names_or_guess_event_identity():
    corpus = corpus_from_graph(
        graph(
            record("a", 1, {"CUST_ID": "0001"}),
            record("b", 1, {"CUST_ID": "0001"}),
            record("c", 1, {}, kind="客户"),
            record("d", 1, {}, kind="客户"),
        )
    )
    assert pairs(corpus) == set()
    assert len(corpus.mentions) == 4
    assert all(m.recall is not None and not m.recall.names for m in corpus.mentions)


def test_real_customer_names_identifiers_and_prior_observations_still_match():
    prior = record("prior", 1, {"CUST_ID": "0001"}, kind="客户", name="合成甲企业")
    merged = record("a", 2, {}, kind="客户")
    merged["resolution"] = {"source_nodes": [prior]}
    corpus = corpus_from_graph(
        graph(
            merged,
            record("b", 3, {"CUST_ID": "0001"}, kind="客户", name="甲企业简称"),
            record("c", 4, {}, kind="客户", name="合成甲企业"),
        )
    )
    assert pairs(corpus) >= {("a", "b"), ("a", "c")}
    assert '"CUST_ID": "0001"' in corpus.mentions[0].context


def test_text_event_mentions_keep_original_recall_contract():
    source = graph(event("a", 1), event("b", 2))
    source["source_kind"] = "graphrag"
    for node in source["nodes"]:
        node["name"] = "合成活动"
        node["source_context"] = "合成活动在上海举办，由该组织负责。"
    corpus = corpus_from_graph(source)
    assert all(m.recall is None for m in corpus.mentions)
    assert all("recall" not in row for row in corpus.to_dict()["mentions"])
    assert pairs(corpus) == {("a", "b")}


def test_structured_event_candidates_get_model_budget_before_similar_customer_names():
    corpus = corpus_from_graph(
        graph(
            record("a", 1, {}, kind="客户", name="合成客户甲"),
            record("b", 2, {}, kind="客户", name="合成客户乙"),
            event("y", 1),
            event("z", 2),
        )
    )

    class Judge:
        version = "test"
        seen = []

        async def judge(self, left, right):
            self.seen.append((left.mention_id, right.mention_id))
            return {"verdict": "uncertain", "reason": "需要进一步核对"}

    judge = Judge()
    asyncio.run(
        resolve_evidence(
            corpus,
            ResolverConfig(
                retrieval_policy="balanced",
                max_model_calls=1,
            ),
            judge,
        )
    )
    assert judge.seen == [("y", "z")]


def test_reported_18678_node_scale_keeps_records_and_duplicate_event_under_default_budget():
    nodes = [
        event(f"e{i}", i, customer=f"{i % 1000:04}", kind=f"event{i // 1000}") for i in range(16678)
    ]
    nodes.extend(record(f"r{i}", i, {}, kind="客户") for i in range(1000))
    nodes.extend(
        record(f"c{i}", i, {"cust_id": str(i)}, kind="客户", name=f"customer{i}")
        for i in range(1000)
    )
    # A genuine duplicate observation at a different row must survive the fix.
    nodes[-1] = event("duplicate", 999, customer="0000", kind="event0")
    source = graph(*nodes)
    before = digest(source)
    corpus = corpus_from_graph(source)
    assert len(corpus.mentions) == 18678
    assert pairs(corpus, candidate_limit=10, max_pairs=20000) == {("duplicate", "e0")}
    assert digest(source) == before


@pytest.mark.parametrize("policy", ["original", "balanced"])
def test_budget_guard_remains_explicit_and_never_silently_truncates(policy):
    corpus = corpus_from_graph(graph(*(event(str(i), i) for i in range(4))))
    with pytest.raises(CandidateBudgetExceeded) as error:
        pairs(corpus, policy, max_pairs=1)
    assert error.value.selected_pairs > 1
    assert error.value.max_pairs == 1
    assert "原图未修改" in str(error.value)


def test_budget_failure_persists_diagnostics_and_never_calls_pair_model(tmp_path):
    source = graph(*(event(str(i), i) for i in range(4)))
    service = ResolutionService(Settings(_env_file=None, data_dir=tmp_path), lambda *_: source)

    class Model:
        configured = True
        calls = 0

        async def complete(self, *_):
            self.calls += 1
            raise AssertionError("Budget validation must precede pair model calls")

    service.model = Model()

    async def run():
        started = await service.start(
            StartRequest(
                source_kind="database",
                source_id="version",
                options={"max_pairs": 1},
            )
        )
        await asyncio.gather(*service.tasks)
        return service.get(started.id)

    result = asyncio.run(run())
    assert result.status == "failed" and service.model.calls == 0
    assert "超过上限" in result.error
    assert result.diagnostics["candidate_budget"]["max_pairs"] == 1
    assert result.diagnostics["candidate_budget"]["selected_pairs_at_least"] > 1
    assert result.summary.original_node_count == result.summary.node_count == 4
