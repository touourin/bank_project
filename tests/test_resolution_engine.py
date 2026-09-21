# Copyright (c) 2026 Microsoft Corporation.
# Licensed under the MIT License

"""Migrated core regressions; transport/graph integration is covered separately."""

import asyncio
from functools import wraps

import pytest

from bank_project.resolution.engine.contracts import Corpus, ResolverConfig
from bank_project.resolution.engine.resolver import resolve_evidence


def synchronous(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


def corpus(count=2, identifiers=None):
    rows = []
    for index in range(count):
        mid = f"m{index}"
        context = f"李明在示例文档{index}出现，其测试登记编号为ID-{index}。"
        row = {
            "mention_id": mid,
            "name": "李明",
            "type": "PERSON",
            "source_id": f"doc{index}",
            "context": context,
        }
        if identifiers is not None:
            value = identifiers[index]
            row["context"] = f"李明的测试登记编号为{value}，仅用于回归测试。"
            row["identifiers"] = [
                {
                    "namespace": "test",
                    "value": value,
                    "quote": row["context"],
                    "verified": True,
                }
            ]
        rows.append(row)
    return Corpus.from_dict(
        {
            "schema_version": "er-corpus-v1",
            "namespace": "test",
            "mentions": rows,
        }
    )


class Judge:
    version = "mock-v1"

    def __init__(self, verdicts=None, invalid=False):
        self.verdicts = verdicts or {}
        self.invalid = invalid
        self.calls = 0

    async def judge(self, left, right):
        self.calls += 1
        return {
            "verdict": self.verdicts.get((left.mention_id, right.mention_id), "same"),
            "left_quote": "invented evidence" if self.invalid else left.context,
            "right_quote": right.context,
            "reason": "Mock identity evidence",
        }


def memberships(result):
    return {row["mention_id"]: row["entity_id"] for row in result.memberships}


@synchronous
async def test_verified_identifier_merges_aliases():
    data = corpus(identifiers=["A", "A"]).to_dict()
    data["mentions"][1]["name"] = "Different Alias"
    result = await resolve_evidence(
        Corpus.from_dict(data), ResolverConfig(unique_id_namespaces=("test",))
    )
    assert len(result.entities) == 1
    assert result.decisions[0]["origin"] == "rule"


@synchronous
async def test_identifier_namespace_requires_explicit_policy():
    result = await resolve_evidence(corpus(identifiers=["A", "A"]))
    assert len(result.entities) == 2


@synchronous
async def test_unreviewed_identifier_does_not_hard_merge():
    data = corpus(identifiers=["A", "A"]).to_dict()
    data["mentions"][1]["identifiers"][0]["verified"] = False
    result = await resolve_evidence(
        Corpus.from_dict(data), ResolverConfig(unique_id_namespaces=("test",))
    )
    assert len(result.entities) == 2


@synchronous
async def test_identifier_conflict_overrides_model_without_calling_it():
    judge = Judge()
    result = await resolve_evidence(
        corpus(identifiers=["A", "B"]),
        ResolverConfig(unique_id_namespaces=("test",), model_policy="apply"),
        judge,
    )
    assert len(result.entities) == 2
    assert judge.calls == 0


@synchronous
async def test_internal_identifier_conflict_rejected():
    data = corpus(identifiers=["A", "B"]).to_dict()
    row = data["mentions"][0]
    row["context"] += "编号B也被错误标注。"
    row["identifiers"].append(
        {
            "namespace": "test",
            "value": "B",
            "quote": "编号B也被错误标注。",
            "verified": True,
        }
    )
    with pytest.raises(ValueError, match="Conflicting"):
        await resolve_evidence(
            Corpus.from_dict(data), ResolverConfig(unique_id_namespaces=("test",))
        )


@synchronous
async def test_model_matches_require_explicit_apply_policy():
    result = await resolve_evidence(corpus(), judge=Judge())
    assert len(result.entities) == 2
    assert result.decisions[0]["reason"] == "MODEL_MATCH_REQUIRES_REVIEW"
    applied = await resolve_evidence(corpus(), ResolverConfig(model_policy="apply"), Judge())
    assert len(applied.entities) == 1


@synchronous
async def test_invented_model_quote_is_deferred():
    result = await resolve_evidence(
        corpus(), ResolverConfig(model_policy="apply"), Judge(invalid=True)
    )
    assert len(result.entities) == 2
    assert result.decisions[0]["reason"] == "JUDGE_FAILED"


@synchronous
async def test_model_timeout_is_deferred():
    class SlowJudge(Judge):
        async def judge(self, left, right):
            await asyncio.sleep(1)

    result = await resolve_evidence(corpus(), ResolverConfig(timeout_seconds=0.001), SlowJudge())
    assert result.decisions[0]["error_type"] == "TimeoutError"


@synchronous
async def test_transitive_similarity_does_not_cross_negative_identity():
    judge = Judge({("m0", "m2"): "different"})
    result = await resolve_evidence(corpus(3), ResolverConfig(model_policy="apply"), judge)
    mapping = memberships(result)
    assert mapping["m0"] == mapping["m1"]
    assert mapping["m0"] != mapping["m2"]
    assert result.diagnostics["blocked_merges"]


@synchronous
async def test_cannot_link_is_respected():
    data = corpus(3).to_dict()
    data["constraints"] = [
        {
            "left": "m0",
            "right": "m2",
            "verdict": "different",
            "reviewed": True,
            "reason": "Reviewed different people",
        }
    ]
    result = await resolve_evidence(
        Corpus.from_dict(data), ResolverConfig(model_policy="apply"), Judge()
    )
    assert memberships(result)["m0"] != memberships(result)["m2"]


@synchronous
async def test_model_budget_does_not_fall_back_to_name_merge():
    result = await resolve_evidence(
        corpus(3), ResolverConfig(model_policy="apply", max_model_calls=1), Judge()
    )
    assert result.diagnostics["judge_requests"] == 1
    assert len(result.entities) == 2
    assert any(row["reason"] == "MODEL_BUDGET_EXHAUSTED" for row in result.decisions)


@synchronous
async def test_progress_counts_failed_and_budget_skipped_pairs_as_processed():
    events = []

    async def report(*values):
        events.append(values)

    result = await resolve_evidence(
        corpus(3),
        ResolverConfig(max_model_calls=1),
        Judge(invalid=True),
        on_progress=report,
    )
    assert events[0] == (0, 3, 0, 0) and events[-1] == (3, 3, 1, 2)
    assert [event[0] for event in events] == [0, 1, 2, 3]
    assert len(result.decisions) == 3 and len(result.entities) == 3


@synchronous
async def test_alias_progress_reports_failures_and_budget_without_losing_mentions():
    from bank_project.resolution.engine.synonyms import expand_corpus

    events = []

    async def report(*values):
        events.append(values)

    class FailedAliases:
        async def expand_aliases(self, mention):
            raise TimeoutError("Synthetic timeout")

    enriched, _ = await expand_corpus(
        corpus(3), FailedAliases(), ResolverConfig(), max_alias_calls=1, on_progress=report
    )
    assert events[0] == (0, 3, 0, 0) and events[-1] == (3, 3, 1, 2)
    assert [event[0] for event in events] == [0, 1, 2, 3]
    assert enriched == corpus(3)


@synchronous
async def test_candidate_overflow_blocks_model_auto_merge():
    result = await resolve_evidence(
        corpus(3), ResolverConfig(model_policy="apply", candidate_limit=1), Judge()
    )
    assert len(result.entities) == 3
    assert len(result.diagnostics["candidate_overflow"]) == 3


@synchronous
async def test_same_corpus_replay_is_deterministic():
    value = corpus(identifiers=["A", "A"])
    config = ResolverConfig(unique_id_namespaces=("test",))
    first = await resolve_evidence(value, config)
    second = await resolve_evidence(value, config)
    assert first == second
    data = value.to_dict()
    data["mentions"].reverse()
    reordered = await resolve_evidence(Corpus.from_dict(data), config)
    assert first.memberships == reordered.memberships


def test_skipped_frequent_description_block_is_reported_as_incomplete():
    from bank_project.resolution.engine.candidates import retrieve

    data = corpus(3).to_dict()
    for index, mention in enumerate(data["mentions"]):
        mention["name"] = chr(ord("a") + index)
        mention["description"] = "相同描述"
    candidates, overflow = retrieve(Corpus.from_dict(data), ResolverConfig(max_block_size=2))
    assert candidates == {"m0": [], "m1": [], "m2": []}
    assert overflow == {"m0", "m1", "m2"}
