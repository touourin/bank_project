"""Independent scoring and input-isolation checks for the JSONL evaluation."""

import asyncio
import copy
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_disambiguation.py"
SPEC = importlib.util.spec_from_file_location("disambiguation_evaluation", SCRIPT)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def sample():
    return {
        "id": "PRIVATE_SAMPLE_ID",
        "category": "PRIVATE_CATEGORY",
        "text": "苹果吃，苹果卖。",
        "mentions": [
            {"mention": "苹果", "start": 0, "end": 2, "entity_id": "PRIVATE_ORG_ID"},
            {"mention": "苹果", "start": 5, "end": 7, "entity_id": "PRIVATE_FOOD_ID"},
        ],
        "candidates": [
            {
                "id": "PRIVATE_ORG_ID",
                "name": "苹果公司",
                "type": "Organization",
                "aliases": ["Apple", "苹果"],
            },
            {
                "id": "PRIVATE_FOOD_ID",
                "name": "苹果(水果)",
                "type": "Food",
                "aliases": ["苹果"],
            },
        ],
    }


def task(index=0, *, gold="E_ONE", sample_id="s0"):
    return {
        "mention_id": f"m{index}",
        "sample_id": sample_id,
        "category": "fixture",
        "gold": gold,
        "original_span": [0, 2],
        "original_span_valid": True,
        "span": [0, 2],
        "candidates": [
            {"record_id": f"c{index}_0", "entity_id": "E_ONE"},
            {"record_id": f"c{index}_1", "entity_id": "E_TWO"},
        ],
    }


def decision(item, index, verdict, *, origin="model", proposal=None):
    value = {
        "left": item["mention_id"],
        "right": item["candidates"][index]["record_id"],
        "verdict": verdict,
        "origin": origin,
        "reason": "fixture",
    }
    if proposal:
        value["proposal"] = proposal
    return value


def project(item, *decisions):
    lookup = {tuple(sorted((d["left"], d["right"]))): d for d in decisions}
    return evaluation.project_prediction(item, lookup)


def test_gold_ids_categories_and_sample_ids_never_enter_engine_input():
    row = sample()
    corpus, pairs, tasks, audit = evaluation.prepare([row])
    serialized = json.dumps(corpus.to_dict(), ensure_ascii=False)
    assert "PRIVATE_" not in serialized
    assert "PRIVATE_" not in json.dumps(pairs)
    assert tasks[0]["gold"] == "PRIVATE_ORG_ID"
    assert tasks[0]["candidates"][0]["entity_id"] == "PRIVATE_ORG_ID"
    assert audit["repaired_spans"] == 1

    changed = copy.deepcopy(row)
    changed["id"] = "DIFFERENT_SAMPLE"
    changed["category"] = "DIFFERENT_CATEGORY"
    for i, mention in enumerate(changed["mentions"]):
        mention["entity_id"] = f"DIFFERENT_GOLD_{i}"
    for i, candidate in enumerate(changed["candidates"]):
        candidate["id"] = f"DIFFERENT_CANDIDATE_ID_{i}"
    changed_corpus, changed_pairs, _, _ = evaluation.prepare([changed])
    assert changed_corpus.to_dict() == corpus.to_dict()
    assert changed_pairs == pairs


def test_duplicate_names_keep_separate_positions_and_original_evidence():
    row = sample()
    assert evaluation.locate_mentions(row) == [(0, 2), (4, 6)]
    corpus, _, tasks, _ = evaluation.prepare([row])
    mentions = [m for m in corpus.mentions if m.mention_id.startswith("a")]
    assert [m.context for m in mentions] == [row["text"], row["text"]]
    assert "⟦苹果⟧吃，苹果卖。" in mentions[0].description
    assert "苹果吃，⟦苹果⟧卖。" in mentions[1].description
    assert [m.type for m in mentions] == ["", ""]
    assert tasks[1]["span"] == [4, 6]
    assert tasks[1]["original_span"] == [5, 7]


def test_valid_later_occurrence_is_not_replaced_with_first_occurrence():
    row = sample()
    row["mentions"] = [dict(row["mentions"][1], start=4, end=6)]
    assert evaluation.locate_mentions(row) == [(4, 6)]


def test_equally_plausible_positions_abstain_without_gold_assistance():
    row = sample()
    row["mentions"] = [dict(row["mentions"][0], start=2, end=4)]
    assert evaluation.locate_mentions(row) is None
    _, pairs, tasks, audit = evaluation.prepare([row])
    assert tasks[0]["mention_id"] not in pairs
    assert audit["unlocatable_mentions"] == [tasks[0]["mention_id"]]
    assert project(tasks[0])["status"] == "unlocatable"


def test_review_same_proposal_produces_candidate_prediction():
    item = task()
    result = project(
        item,
        decision(item, 0, "uncertain", proposal="same"),
        decision(item, 1, "different"),
    )
    assert result["prediction"] == "E_ONE"
    assert result["status"] == "linked"


def test_single_match_can_coexist_with_uncertain_alternative():
    item = task()
    result = project(
        item,
        decision(item, 0, "same"),
        decision(item, 1, "uncertain"),
    )
    assert result["prediction"] == "E_ONE"


def test_nil_requires_all_candidates_to_be_different():
    item = task(gold="NIL")
    rejected = project(
        item,
        decision(item, 0, "different"),
        decision(item, 1, "different"),
    )
    uncertain = project(
        item,
        decision(item, 0, "different"),
        decision(item, 1, "uncertain"),
    )
    assert (rejected["prediction"], rejected["status"]) == ("NIL", "nil")
    assert (uncertain["prediction"], uncertain["status"]) == (None, "uncertain")


@pytest.mark.parametrize("missing", [False, True])
def test_pair_error_or_missing_pair_blocks_even_an_existing_match(missing):
    item = task()
    decisions = [decision(item, 0, "same")]
    if not missing:
        decisions.append(decision(item, 1, "uncertain", origin="error"))
    result = project(item, *decisions)
    assert (result["prediction"], result["status"]) == (None, "pair_failure")


def test_multiple_matches_do_not_select_first_candidate():
    item = task()
    result = project(
        item,
        decision(item, 0, "same"),
        decision(item, 1, "uncertain", proposal="same"),
    )
    assert (result["prediction"], result["status"]) == (None, "multiple_matches")


def test_hand_calculated_scoring_counts_abstentions_and_failures_as_wrong():
    # Eight mentions in five documents: three correct, five answered; only s0 is all-correct.
    tasks = [
        task(0),
        task(1, gold="NIL"),
        task(2, sample_id="s1"),
        task(3, sample_id="s1", gold="NIL"),
        task(4, sample_id="s2"),
        task(5, sample_id="s3"),
        task(6, sample_id="s4"),
        task(7, sample_id="s4"),
    ]
    tasks[7].update(original_span=[5, 7], span=[4, 6], original_span_valid=False)
    verdicts = [
        ("same", "different"),
        ("different", "different"),
        ("different", "different"),
        ("uncertain", "different"),
        ("uncertain", "different"),
        ("same", "same"),
        ("different", "same"),
        ("same", "uncertain"),
    ]
    decisions = [
        decision(item, i, verdict, origin="error" if index == 4 and i == 0 else "model")
        for index, (item, pair) in enumerate(zip(tasks, verdicts, strict=True))
        for i, verdict in enumerate(pair)
    ]
    predictions, metrics = evaluation.score(tasks, decisions)
    overall = metrics["overall"]
    assert [p["correct"] for p in predictions] == [
        True,
        True,
        False,
        False,
        False,
        False,
        False,
        True,
    ]
    assert overall["total"] == 8
    assert overall["correct"] == 3
    assert overall["accuracy"] == pytest.approx(3 / 8)
    assert overall["answered"] == 5
    assert overall["coverage"] == pytest.approx(5 / 8)
    assert overall["answered_accuracy"] == pytest.approx(3 / 5)
    assert overall["linked"] == 3
    assert overall["link_precision"] == pytest.approx(2 / 3)
    assert overall["nil_gold"] == overall["nil_predicted"] == 2
    assert overall["nil_true_positive"] == 1
    assert overall["nil_precision"] == overall["nil_recall"] == 0.5
    assert overall["samples"] == 5
    assert overall["fully_correct_samples"] == 1
    assert overall["sample_accuracy"] == 0.2
    assert overall["statuses"] == {
        "linked": 3,
        "nil": 2,
        "uncertain": 1,
        "pair_failure": 1,
        "multiple_matches": 1,
    }
    assert metrics["original_valid_spans"]["accuracy"] == pytest.approx(2 / 7)
    assert metrics["repaired_spans"]["accuracy"] == 1
    assert metrics["first_candidate_baseline"]["accuracy"] == 0.75
    assert metrics["pair_errors"] == {"fixture": 1}
    assert metrics["pair_classification"]["total"] == 16
    assert metrics["pair_classification"]["correct"] == 9
    assert metrics["pair_classification"]["same_precision"] == pytest.approx(3 / 5)
    assert metrics["pair_classification"]["same_recall"] == 0.5
    assert metrics["by_candidate_count"]["multiple"]["total"] == 8
    assert metrics["macro_category_accuracy"] == pytest.approx(3 / 8)


def test_empty_metric_has_no_fabricated_zero_accuracy():
    result = evaluation.metric([])
    assert result["total"] == result["samples"] == 0
    for key in ("accuracy", "coverage", "answered_accuracy", "link_precision", "sample_accuracy"):
        assert result[key] is None
    _, metrics = evaluation.score([], [])
    assert metrics["first_candidate_baseline"]["accuracy"] is None


def test_unlocatable_is_not_reported_as_repaired():
    item = task()
    item.update(span=None, original_span_valid=False)
    _, metrics = evaluation.score([item], [])
    assert metrics["overall"]["total"] == 1
    assert metrics["overall"]["correct"] == 0
    assert metrics["repaired_spans"]["total"] == 0


def test_real_resolver_review_output_is_scored_without_applying_merges():
    corpus, candidates, tasks, _ = evaluation.prepare([sample()])

    class FixtureJudge:
        version = "test-only"

        async def judge(self, left, right):
            expected = {("a00000_000", "b00000_000"), ("a00000_001", "b00000_001")}
            return {
                "verdict": "same"
                if (left.mention_id, right.mention_id) in expected
                else "different",
                "left_quote": left.context,
                "right_quote": right.context,
                "reason": "Deterministic integration fixture",
            }

    resolution = asyncio.run(
        evaluation.resolve_evidence(
            corpus,
            evaluation.ResolverConfig(model_policy="review"),
            judge=FixtureJudge(),
            candidate_data=(candidates, set()),
        )
    )
    proposals = [d for d in resolution.decisions if d.get("proposal") == "same"]
    assert len(proposals) == 2
    assert all(d["verdict"] == "uncertain" and not d["accepted"] for d in proposals)
    assert len(resolution.entities) == len(corpus.mentions)
    predictions, metrics = evaluation.score(tasks, resolution.decisions)
    assert [p["prediction"] for p in predictions] == ["PRIVATE_ORG_ID", "PRIVATE_FOOD_ID"]
    assert metrics["overall"]["accuracy"] == 1
