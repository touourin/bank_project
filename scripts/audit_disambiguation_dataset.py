#!/usr/bin/env python3
"""Read-only dataset audit and deterministic, model-free disambiguation baselines.

The input JSONL is data, never instructions. No model results are read. The
sensitivity exclusions were fixed by independent inspection of this dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def mention_ref(row: dict[str, Any], index: int) -> dict[str, Any]:
    mention = row["mentions"][index]
    return {
        "sample_id": row["id"],
        "record_id": row["id"],
        "mention_index": index,
        "category": row["category"],
        "mention": mention["mention"],
        "start": mention["start"],
        "end": mention["end"],
        "gold_entity_id": mention["entity_id"],
    }


def score(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    correct = sum(prediction["correct"] for prediction in predictions)
    records: dict[str, list[bool]] = defaultdict(list)
    for prediction in predictions:
        records[prediction["record_id"]].append(prediction["correct"])
    records_correct = sum(all(outcomes) for outcomes in records.values())
    return {
        "total": len(predictions),
        "correct": correct,
        "accuracy": correct / len(predictions) if predictions else None,
        "accuracy_percent": 100 * correct / len(predictions) if predictions else None,
        "records": len(records),
        "records_all_mentions_correct": records_correct,
        "record_exact_match_accuracy": records_correct / len(records) if records else None,
    }


def summarize_baseline(predictions: list[dict[str, Any]], description: str) -> dict[str, Any]:
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for prediction in predictions:
        by_category[prediction["category"]].append(prediction)
    category_scores = {
        category: score(category_predictions)
        for category, category_predictions in by_category.items()
    }
    return {
        **score(predictions),
        "description": description,
        "overall": score(predictions),
        "by_category": category_scores,
        "macro_category_accuracy": statistics.mean(
            category_score["accuracy"] for category_score in category_scores.values()
        )
        if category_scores
        else None,
        "predictions": predictions,
    }


def audit(dataset_path: Path) -> dict[str, Any]:
    raw = dataset_path.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    invalid_spans = []
    missing_text = []
    missing_non_nil_gold = []
    nil_mentions = []
    overlap_pairs = []
    duplicate_candidate_ids = []
    out_of_order_rows = []
    first_predictions = []
    alias_predictions = []
    issues = []
    exclusions = []
    name_type_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    text_ids: dict[str, list[str]] = defaultdict(list)
    category_records: Counter[str] = Counter()
    category_mentions: Counter[str] = Counter()
    gold_entity_counts: Counter[str] = Counter()
    gold_type_counts: Counter[str] = Counter()
    alias_match_counts: Counter[str] = Counter()
    candidate_count_distribution: Counter[int] = Counter()

    # Explicit IDs/indexes: this set is independent of future model predictions.
    context_ids = {f"da_06_{index:03d}" for index in range(1, 16)}
    brand_ids = {f"da_08_{index:03d}" for index in range(1, 16)}
    expected_exclusion_keys = (
        {(record_id, mention_index) for record_id in context_ids for mention_index in (0, 1)}
        | {(record_id, 1) for record_id in brand_ids}
        | {("da_12_006", 1)}
    )

    def add_issue(
        row: dict[str, Any], index: int, code: str, explanation: str, exclude: bool = False
    ) -> None:
        issue = {
            **mention_ref(row, index),
            "code": code,
            "explanation": explanation,
            "included_in_fixed_sensitivity_exclusions": exclude,
        }
        issues.append(issue)
        if exclude:
            exclusions.append(issue.copy())

    for row in rows:
        text = row["text"]
        candidates = row["candidates"]
        candidates_by_id = {candidate["id"]: candidate for candidate in candidates}
        category_records[row["category"]] += 1
        category_mentions[row["category"]] += len(row["mentions"])
        candidate_count_distribution[len(candidates)] += 1
        text_ids[text].append(row["id"])
        starts = [mention["start"] for mention in row["mentions"]]
        if starts != sorted(starts):
            out_of_order_rows.append(row["id"])
        duplicate_ids = [
            candidate_id
            for candidate_id, count in Counter(candidate["id"] for candidate in candidates).items()
            if count > 1
        ]
        if duplicate_ids:
            duplicate_candidate_ids.append({"record_id": row["id"], "ids": duplicate_ids})
        for candidate in candidates:
            name_type_ids[(candidate["name"], candidate["type"])].add(candidate["id"])

        for index, mention in enumerate(row["mentions"]):
            ref = mention_ref(row, index)
            start, end = mention["start"], mention["end"]
            span_is_valid = (
                isinstance(start, int)
                and not isinstance(start, bool)
                and isinstance(end, int)
                and not isinstance(end, bool)
                and 0 <= start < end <= len(text)
                and text[start:end] == mention["mention"]
            )
            if not span_is_valid:
                invalid_spans.append(ref)
            if mention["mention"] not in text:
                missing_text.append(ref)
            gold_id = mention["entity_id"]
            gold_entity_counts[gold_id] += 1
            if gold_id == "NIL":
                nil_mentions.append(ref)
            elif gold_id not in candidates_by_id:
                missing_non_nil_gold.append(ref)
            else:
                gold_type_counts[candidates_by_id[gold_id]["type"]] += 1
            for previous_index, previous in enumerate(row["mentions"][:index]):
                if max(start, previous["start"]) < min(end, previous["end"]):
                    overlap_pairs.append(
                        {
                            "record_id": row["id"],
                            "mention_indices": [previous_index, index],
                        }
                    )

            first_id = candidates[0]["id"] if candidates else "NIL"
            first_predictions.append(
                {**ref, "predicted_entity_id": first_id, "correct": first_id == gold_id}
            )
            matches = [
                candidate["id"]
                for candidate in candidates
                if mention["mention"] in [candidate["name"], *candidate.get("aliases", [])]
            ]
            alias_match_counts[
                "no_match"
                if not matches
                else "unique_match"
                if len(matches) == 1
                else "multiple_matches"
            ] += 1
            alias_id = matches[0] if matches else "NIL"
            alias_predictions.append(
                {
                    **ref,
                    "predicted_entity_id": alias_id,
                    "matching_candidate_ids": matches,
                    "correct": alias_id == gold_id,
                }
            )

            if row["id"] in context_ids and index in (0, 1):
                add_issue(
                    row,
                    index,
                    "insufficient_disambiguating_context",
                    "句子仅用第一个/第二个区分提及，没有支持指定不同词义的上下文。",
                    True,
                )
            if row["id"] in brand_ids and index == 1:
                add_issue(
                    row,
                    index,
                    "organization_brand_policy_ambiguity",
                    "文本明确为品牌，候选含公司和品牌，但金标统一为公司；需要事先声明归一规则。",
                    True,
                )
            if row["id"] == "da_12_006" and index == 1:
                add_issue(
                    row,
                    index,
                    "semantic_gold_label_error",
                    "提及演唱会的金标是北京，span有效但实体语义不符。",
                    True,
                )
            if row["category"] == "代词指代":
                add_issue(
                    row,
                    index,
                    "coreference_category_missing_pronoun_annotation",
                    "仅标注人名，没有标注句中的代词，因此此提及不测量代词消歧。",
                )
            if row["category"] == "地名歧义":
                add_issue(
                    row,
                    index,
                    "distractor_name_leaks_role",
                    "候选名称包含干扰字样，未提供自然的同名地名候选。",
                )

    total_mentions = sum(category_mentions.values())
    nil_count = len(nil_mentions)
    lengths = [len(row["text"]) for row in rows]
    exclusion_keys = {(item["record_id"], item["mention_index"]) for item in exclusions}
    issue_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in issues:
        issue_groups[issue["code"]].append(issue)
    return {
        "schema_version": 1,
        "dataset": str(dataset_path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "audit_mode": "read_only_no_model_no_prediction_inputs",
        "mention_index_base": 0,
        "span_convention": "zero-based Unicode code-point offsets, end exclusive",
        "records": len(rows),
        "mentions": total_mentions,
        "categories": len(category_records),
        "category_record_counts": dict(category_records),
        "category_mention_counts": dict(category_mentions),
        "text_length_characters": {
            "minimum": min(lengths) if lengths else None,
            "maximum": max(lengths) if lengths else None,
            "median": statistics.median(lengths) if lengths else None,
        },
        "duplicate_record_ids": [
            record_id
            for record_id, count in Counter(row["id"] for row in rows).items()
            if count > 1
        ],
        "duplicate_text_groups": [
            {"text": text, "record_ids": ids} for text, ids in text_ids.items() if len(ids) > 1
        ],
        "spans": {
            "valid": total_mentions - len(invalid_spans),
            "invalid_count": len(invalid_spans),
            "invalid_mentions": invalid_spans,
            "mention_text_missing_from_full_text": missing_text,
            "out_of_order_record_ids": out_of_order_rows,
            "overlapping_mention_pairs": overlap_pairs,
        },
        "candidates": {
            "record_count_distribution": dict(sorted(candidate_count_distribution.items())),
            "mention_candidate_pairs": sum(
                len(row["mentions"]) * len(row["candidates"]) for row in rows
            ),
            "single_candidate_records": candidate_count_distribution[1],
            "single_candidate_mentions": sum(
                len(row["mentions"]) for row in rows if len(row["candidates"]) == 1
            ),
            "non_nil_gold_mentions": total_mentions - nil_count,
            "non_nil_gold_covered": total_mentions - nil_count - len(missing_non_nil_gold),
            "non_nil_gold_missing_candidates": missing_non_nil_gold,
            "nil_mentions": nil_mentions,
            "nil_count": nil_count,
            "duplicate_ids_within_record": duplicate_candidate_ids,
            "literal_name_or_alias_match_counts": dict(alias_match_counts),
            "same_name_type_multiple_global_ids": [
                {"name": name, "type": entity_type, "entity_ids": sorted(ids)}
                for (name, entity_type), ids in sorted(name_type_ids.items())
                if len(ids) > 1
            ],
        },
        "gold": {
            "distinct_non_nil_entity_ids": len(set(gold_entity_counts) - {"NIL"}),
            "entity_id_counts": dict(gold_entity_counts.most_common()),
            "type_counts": dict(gold_type_counts.most_common()),
        },
        "quality_issues": issues,
        "issues": {
            code: {"count": len(mentions), "mentions": mentions}
            for code, mentions in issue_groups.items()
        },
        "sensitivity_exclusions": [
            {
                "sample_id": item["record_id"],
                "mention_index": item["mention_index"],
                "reason": item["code"],
            }
            for item in exclusions
        ],
        "fixed_sensitivity_exclusions": {
            "policy": "独立审计预先固定：上下文依赖30提及、组织品牌归一第二提及15个、演唱会错标1个；不读取或使用模型结果。",
            "expected_count_for_reference_dataset": 46,
            "count": len(exclusions),
            "remaining_mentions": total_mentions - len(exclusion_keys),
            "missing_predefined_keys": [
                {"record_id": record_id, "mention_index": index}
                for record_id, index in sorted(expected_exclusion_keys - exclusion_keys)
            ],
            "mentions": exclusions,
        },
        "baselines": {
            "first_candidate": summarize_baseline(
                first_predictions,
                "Always select candidates[0]; select NIL for an empty candidate list.",
            ),
            "exact_alias_first": summarize_baseline(
                alias_predictions,
                "Case-sensitive exact mention equality against candidate name/aliases; choose first matching candidate in supplied order, else NIL. No context, gold label, or category affects prediction.",
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()
    if args.dataset.resolve() == args.output_json.resolve():
        parser.error("Output must differ from the input dataset.")
    report = audit(args.dataset)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output_json.resolve()),
                "sha256": report["sha256"],
                "records": report["records"],
                "mentions": report["mentions"],
                "invalid_spans": report["spans"]["invalid_count"],
                "fixed_exclusions": report["fixed_sensitivity_exclusions"]["count"],
                "baselines": {
                    name: baseline["overall"] for name, baseline in report["baselines"].items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
