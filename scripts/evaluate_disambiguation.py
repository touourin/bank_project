"""Evaluate the existing pair resolver against a supplied mention/candidate JSONL.

This is conditional entity linking with supplied mentions/candidates, not a
GraphRAG extraction or candidate-retrieval benchmark. Gold never enters Corpus.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import httpx

from bank_project.alignment.model_client import JsonModel
from bank_project.resolution.engine.contracts import Corpus, Mention, ResolverConfig, digest
from bank_project.resolution.engine.model_judge import JUDGE_REVISION
from bank_project.resolution.engine.resolver import resolve_evidence
from bank_project.resolution.engine.runner import write_json
from bank_project.resolution.service import CompletionAdapter, SourceModelJudge
from bank_project.settings import Settings


def locate_mentions(row):
    """Repair offsets using only strings/order, preserving all valid original spans.

    Minimize absolute start/end displacement subject to ordered, nonoverlapping
    mentions. Reject ties instead of consulting identities or candidate order.
    """
    text = row["text"]
    choices = []
    for mention in row["mentions"]:
        start, end = mention["start"], mention["end"]
        valid = 0 <= start < end <= len(text) and text[start:end] == mention["mention"]
        choices.append(
            [(start, end)]
            if valid
            else [
                (m.start(), m.start() + len(mention["mention"]))
                for m in re.finditer(f"(?={re.escape(mention['mention'])})", text)
            ]
        )

    @lru_cache(None)
    def solve(index, previous_end):
        if index == len(choices):
            return 0, 1, ()
        best_cost, count, best_path = float("inf"), 0, ()
        original = row["mentions"][index]
        for start, end in choices[index]:
            if start < previous_end:
                continue
            cost, ways, path = solve(index + 1, end)
            cost += abs(start - original["start"]) + abs(end - original["end"])
            if ways and cost < best_cost:
                best_cost, count, best_path = cost, ways, ((start, end), *path)
            elif ways and cost == best_cost:
                count = min(2, count + ways)
        return best_cost, count, best_path

    _, count, path = solve(0, 0)
    return list(path) if count == 1 else None


def prepare(rows, *, legacy_input=False):
    """Return label-free engine inputs and a strictly separate scoring sidecar."""
    records, tasks, repairs, unlocatable = [], [], [], []
    pairs = {}
    for row_index, row in enumerate(rows):
        locations = locate_mentions(row)
        candidates = []
        for candidate_index, candidate in enumerate(row["candidates"]):
            cid = f"b{row_index:05d}_{candidate_index:03d}"
            # This is supplied catalogue evidence, never presented as document text.
            context = "候选目录记录（由评测数据提供）：\n" + json.dumps(
                {key: candidate[key] for key in ("name", "type", "aliases")},
                ensure_ascii=False,
            )
            records.append(
                Mention(
                    mention_id=cid,
                    name=candidate["name"],
                    type=candidate["type"],
                    source_id=f"catalog{row_index:05d}",
                    context=context,
                    aliases=tuple(candidate["aliases"]),
                    evidence_kind="source" if legacy_input else "catalog",
                )
            )
            pairs[cid] = []
            candidates.append({"record_id": cid, "entity_id": candidate["id"]})
        for index, mention in enumerate(row["mentions"]):
            mid = f"a{row_index:05d}_{index:03d}"
            original = [mention["start"], mention["end"]]
            span = list(locations[index]) if locations is not None else None
            span_valid = (
                0 <= original[0] < original[1] <= len(row["text"])
                and row["text"][original[0] : original[1]] == mention["mention"]
            )
            task = {
                "mention_id": mid,
                "sample_id": row["id"],
                "category": row["category"],
                "mention_index": index,
                "mention": mention["mention"],
                "text": row["text"],
                "gold": mention["entity_id"],
                "candidates": candidates,
                "original_span": original,
                "original_span_valid": span_valid,
                "span": span,
            }
            tasks.append(task)
            if span is None:
                unlocatable.append(mid)
                continue
            if original != span:
                repairs.append(
                    {
                        "mention_id": mid,
                        "sample_id": row["id"],
                        "mention": mention["mention"],
                        "original": original,
                        "repaired": span,
                    }
                )
            start, end = span
            marked = row["text"][:start] + "⟦" + row["text"][start:end] + "⟧" + row["text"][end:]
            records.append(
                Mention(
                    mention_id=mid,
                    name=mention["mention"],
                    type="",
                    source_id=f"s{row_index:05d}",
                    context=row["text"],
                    source_span=None if legacy_input else (start, end),
                    description=(
                        f"提及定位：context 的字符区间 [{start}, {end})。"
                        f"以下 ⟦⟧ 仅标记当前待比较提及，不属于原文：{marked}"
                    ),
                )
            )
            pairs[mid] = [candidate["record_id"] for candidate in candidates]
            for candidate in candidates:
                pairs[candidate["record_id"]].append(mid)
    corpus = Corpus(
        namespace="jsonl-disambiguation-evaluation-v1",
        mentions=tuple(records),
        input_kind="mentions",
        metadata={
            "protocol": "given-candidates-existing-pair-judge-v1"
            if legacy_input
            else "given-candidates-located-pair-judge-v2",
            "gold_in_input": False,
        },
    )
    return (
        corpus,
        pairs,
        tasks,
        {
            "samples": len(rows),
            "mentions": len(tasks),
            "categories": dict(Counter(row["category"] for row in rows)),
            "original_valid_spans": sum(task["original_span_valid"] for task in tasks),
            "original_invalid_spans": sum(not task["original_span_valid"] for task in tasks),
            "repaired_spans": len(repairs),
            "repairs": repairs,
            "unlocatable_mentions": unlocatable,
            "nil_mentions": sum(task["gold"] == "NIL" for task in tasks),
            "gold_missing_from_candidates": [
                task["mention_id"]
                for task in tasks
                if task["gold"] != "NIL"
                and task["gold"] not in {c["entity_id"] for c in task["candidates"]}
            ],
        },
    )


def project_prediction(task, decisions):
    """Unique same proposal links; only unanimous different implies NIL.

    Any failed/missing pair makes this mention unresolved. Uncertain negatives
    may coexist with one unique affirmative match, and are disclosed per pair.
    """
    comparisons = []
    if task["span"] is None:
        return {"prediction": None, "status": "unlocatable", "comparisons": comparisons}
    for candidate in task["candidates"]:
        key = tuple(sorted((task["mention_id"], candidate["record_id"])))
        decision = decisions.get(key)
        verdict = (
            "same"
            if decision and decision.get("proposal") == "same"
            else decision["verdict"]
            if decision
            else "missing"
        )
        comparisons.append(
            {"candidate_id": candidate["entity_id"], "verdict": verdict, "decision": decision}
        )
    failed = any(
        item["decision"] is None or item["decision"].get("origin") == "error"
        for item in comparisons
    )
    same = [item["candidate_id"] for item in comparisons if item["verdict"] == "same"]
    if failed:
        prediction, status = None, "pair_failure"
    elif len(same) == 1:
        prediction, status = same[0], "linked"
    elif len(same) > 1:
        prediction, status = None, "multiple_matches"
    elif all(item["verdict"] == "different" for item in comparisons):
        prediction, status = "NIL", "nil"
    else:
        prediction, status = None, "uncertain"
    return {"prediction": prediction, "status": status, "comparisons": comparisons}


def metric(rows):
    total = len(rows)
    correct = sum(row["correct"] for row in rows)
    answered = sum(row["prediction"] is not None for row in rows)
    linked = [row for row in rows if row["status"] == "linked"]
    gold_nil = [row for row in rows if row["gold"] == "NIL"]
    predicted_nil = [row for row in rows if row["prediction"] == "NIL"]
    nil_tp = sum(row["correct"] for row in predicted_nil)
    sample_ids = {row["sample_id"] for row in rows}
    fully_correct = sum(
        all(row["correct"] for row in rows if row["sample_id"] == sample_id)
        for sample_id in sample_ids
    )
    return {
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else None,
        "answered": answered,
        "coverage": answered / total if total else None,
        "answered_accuracy": correct / answered if answered else None,
        "linked": len(linked),
        "link_precision": sum(r["correct"] for r in linked) / len(linked) if linked else None,
        "nil_gold": len(gold_nil),
        "nil_predicted": len(predicted_nil),
        "nil_true_positive": nil_tp,
        "nil_precision": nil_tp / len(predicted_nil) if predicted_nil else None,
        "nil_recall": nil_tp / len(gold_nil) if gold_nil else None,
        "nil_f1": 2 * nil_tp / (len(predicted_nil) + len(gold_nil))
        if predicted_nil or gold_nil
        else None,
        "samples": len(sample_ids),
        "fully_correct_samples": fully_correct,
        "sample_accuracy": fully_correct / len(sample_ids) if sample_ids else None,
        "statuses": dict(Counter(row["status"] for row in rows)),
    }


def score(tasks, decisions):
    lookup = {tuple(sorted((d["left"], d["right"]))): d for d in decisions}
    predictions = []
    for task in tasks:
        prediction = project_prediction(task, lookup)
        predictions.append(
            {**task, **prediction, "correct": prediction["prediction"] == task["gold"]}
        )
    categories = dict.fromkeys(task["category"] for task in tasks)
    first_candidate_correct = sum(
        bool(task["candidates"]) and task["candidates"][0]["entity_id"] == task["gold"]
        for task in tasks
    )
    metrics = {
        "overall": metric(predictions),
        "by_category": {
            category: metric([row for row in predictions if row["category"] == category])
            for category in categories
        },
        "original_valid_spans": metric([row for row in predictions if row["original_span_valid"]]),
        "repaired_spans": metric(
            [
                row
                for row in predictions
                if row["span"] is not None and row["span"] != row["original_span"]
            ]
        ),
        "by_candidate_count": {
            "one": metric([row for row in predictions if len(row["candidates"]) == 1]),
            "multiple": metric([row for row in predictions if len(row["candidates"]) >= 2]),
        },
        "first_candidate_baseline": {
            "correct": first_candidate_correct,
            "total": len(tasks),
            "accuracy": first_candidate_correct / len(tasks) if tasks else None,
        },
        "pair_verdicts": dict(
            Counter("same" if d.get("proposal") == "same" else d["verdict"] for d in decisions)
        ),
        "pair_errors": dict(
            Counter(d.get("error_code", d["reason"]) for d in decisions if d["origin"] == "error")
        ),
    }
    category_accuracies = [m["accuracy"] for m in metrics["by_category"].values()]
    metrics["macro_category_accuracy"] = (
        sum(category_accuracies) / len(category_accuracies) if category_accuracies else None
    )
    pair_counts = Counter()
    for task in tasks:
        for candidate in task["candidates"]:
            decision = lookup.get(tuple(sorted((task["mention_id"], candidate["record_id"]))))
            truth = "same" if candidate["entity_id"] == task["gold"] else "different"
            pred = (
                "same"
                if decision and decision.get("proposal") == "same"
                else decision["verdict"]
                if decision
                else "uncertain"
            )
            pair_counts[truth, pred] += 1
    tp, fp = pair_counts["same", "same"], pair_counts["different", "same"]
    total_pairs = sum(pair_counts.values())
    gold_positives = sum(v for (gold, _), v in pair_counts.items() if gold == "same")
    metrics["pair_classification"] = {
        "total": total_pairs,
        "correct": tp + pair_counts["different", "different"],
        "accuracy": (tp + pair_counts["different", "different"]) / total_pairs
        if total_pairs
        else None,
        "same_precision": tp / (tp + fp) if tp + fp else None,
        "same_recall": tp / gold_positives if gold_positives else None,
        "confusion": {
            f"{gold}->{pred}": value for (gold, pred), value in sorted(pair_counts.items())
        },
    }
    return predictions, metrics


def pct(value):
    return f"{value:.2%}" if value is not None else "—"


def render_report(metrics, audit, manifest, predictions):
    result = metrics["overall"]
    lines = [
        "# 实体消歧数据集实测",
        "",
        f"- 模型：`{manifest['provider']}/{manifest['model']}`；现有 `SourceModelJudge` / `evidence_v1`。",
        f"- 提及准确率：**{pct(result['accuracy'])}（{result['correct']}/{result['total']}）**。未决、失败计错。",
        f"- 整条样本全对：{pct(result['sample_accuracy'])}（{result['fully_correct_samples']}/{result['samples']}）。",
        f"- 有明确输出覆盖率：{pct(result['coverage'])}（{result['answered']}/{result['total']}）；明确输出中的准确率：{pct(result['answered_accuracy'])}。",
        f"- 非 NIL 关联精确率：{pct(result['link_precision'])}；NIL precision/recall：{pct(result['nil_precision'])}/{pct(result['nil_recall'])}。",
        f"- 类别宏平均准确率：{pct(metrics['macro_category_accuracy'])}。",
        f"- 调用耗时：{manifest.get('elapsed_seconds', 0):.1f} 秒。",
        "",
        f"- 实际模型调用：{manifest.get('diagnostics', {}).get('completion_calls', '—')}；"
        f"有效缓存命中：{manifest.get('diagnostics', {}).get('judgment_cache_hits', '—')}。"
        "含缓存的耗时不代表首次运行速度。",
        "",
        "## 实现与边界",
        "",
        f"本轮输入契约：`{manifest.get('input_contract', 'legacy')}`；判定版本：`{manifest.get('judge_version', '')}`。",
        "新实现通过 source_span 显式定位提及，校验引用覆盖目标位置；模型选择预先切分的原文证据 ID，系统回填逐字原文。"
        "catalog 目录匹配可利用提供的名称、类型和别名解释词义；两个 source 记录的合并继续要求实体身份证据。"
        "普通 GraphRAG/DB 节点不会自动当作目录。语义相关性仍依赖模型，覆盖目标的长引用本身不是身份已证实。",
        "",
        "评分仍使用原始全部金标，未决/多匹配/失败均计错。公司与品牌仍区分，"
        "不会为了符合这份数据的品牌金标而改变全局合并策略。本集已用于错误分析和优化，"
        "本次是同集回归成绩，不是独立测试集泛化成绩。",
        "",
        "## 口径",
        "",
        "这是一项给定提及和给定候选目录的条件评测。复用项目当前两两身份判定、身份限定规则和逐字引用校验；"
        "跳过候选检索、别名生成和 GraphRAG 抽取，不代表在线端到端准确率。候选 name/type/aliases 是数据集提供的目录事实，"
        "不冒充文档原文。提及侧只提供表面名称、全文和位置，不根据答案填充类型或描述。标准答案、类别和有语义的实体 ID 不发送给模型。",
        "",
        "每次提及与题内所有候选比较。review 模式的唯一 same 建议映射为预测实体；其余候选可为 uncertain。"
        "多项 same 或没有 same 且存在 uncertain 则未决；所有候选明确 different 才预测 NIL。任何一对调用失败则该提及计失败。"
        "这是评测适配层的候选选择规则，不会执行图合并或修改线上数据。",
        "新版本对有 source_span 且有多个 catalog 候选的提及执行全候选竞争判断，多个成对请求共享一次模型调用。"
        "仅选中项产生 same，未选中项保留 uncertain；只有模型明确全部不匹配时才产生全 different/NIL。"
        "因此两两分类指标的负例语义与旧版不同，应以逐提及准确率及覆盖率比较；单候选和来源间合并继续走原两两接口。",
        "",
        f"原始偏移正确 {audit['original_valid_spans']}/{audit['mentions']}，错误 {audit['original_invalid_spans']}。"
        f"按原文逐字匹配、提及顺序和最小偏移距离修复 {audit['repaired_spans']} 处，保留原本有效位置；"
        f"无法唯一定位 {len(audit['unlocatable_mentions'])} 处。未修改原始文件和 entity_id 金标。完整修复清单见 dataset_audit.json。",
        "",
        "## 分类别",
        "",
        "| 类别 | 正确/总数 | 准确率 | 明确输出覆盖率 |",
        "| --- | ---: | ---: | ---: |",
    ]
    if "comparison" in metrics:
        comparison = metrics["comparison"]
        old = comparison["baseline_overall"]
        change = comparison["accuracy_delta"] * 100
        lines[2:2] = [
            f"**同集前后对比：{pct(old['accuracy'])} → {pct(result['accuracy'])}（{change:+.2f} 个百分点）。**",
            f"整条全对率：{pct(old['sample_accuracy'])} → {pct(result['sample_accuracy'])}；"
            f"有效回答覆盖率：{pct(old['coverage'])} → {pct(result['coverage'])}；"
            f"有效回答准确率：{pct(old['answered_accuracy'])} → {pct(result['answered_accuracy'])}。",
            "",
        ]
    for category, item in metrics["by_category"].items():
        lines.append(
            f"| {category} | {item['correct']}/{item['total']} | {pct(item['accuracy'])} | {pct(item['coverage'])} |"
        )
    baseline = metrics["first_candidate_baseline"]
    lines += [
        "",
        "## 数据与解释限制",
        "",
        f"不看原文、总选第一个候选的基线为 {pct(baseline['accuracy'])}（{baseline['correct']}/{baseline['total']}）；候选顺序存在偏置。",
        "",
        "“上下文依赖”使用“第一个/第二个某词”模板，缺少消除词义歧义的语义线索；"
        "“组织品牌归一”的公司和品牌金标统一到公司，而当前引擎明确区分品牌与公司。"
        "这些类别仍按原金标完整计分，未为提高分数而删除或重标。"
        "“代词指代”没有将代词本身作为待评测提及；“长文本多跳”实际为短句，因此类别名称不能证明相应能力。",
        "",
        "明确错标样例：da_12_006 的“演唱会”被标为 E_LOC_BEIJING（北京）；本轮未修改答案。"
        "“地名歧义”的候选名称直接含“干扰”，存在答案提示。跨记录同一名称也有不统一的实体 ID，"
        "因此本次按题内候选评分，不构造跨记录金标簇。",
        "",
        f"原始偏移正确子集：{pct(metrics['original_valid_spans']['accuracy'])}；"
        f"修复位置子集：{pct(metrics['repaired_spans']['accuracy'])}。",
        "",
        f"提及状态：`{json.dumps(result['statuses'], ensure_ascii=False)}`。",
        f"模型/引用失败：`{json.dumps(metrics['pair_errors'], ensure_ascii=False)}`。",
        "",
        "## 补充指标",
        "",
    ]
    for key, label in (("one", "单候选"), ("multiple", "多候选")):
        group = metrics["by_candidate_count"][key]
        lines.append(
            f"- {label}提及：{group['correct']}/{group['total']}，准确率 {pct(group['accuracy'])}。"
        )
    pair = metrics["pair_classification"]
    lines.extend(
        [
            f"- 两两判定准确率（uncertain/错误均计未命中）：{pair['correct']}/{pair['total']}，{pct(pair['accuracy'])}。",
            f"- 两两 same 建议 precision/recall：{pct(pair['same_precision'])}/{pct(pair['same_recall'])}。",
        ]
    )
    if "audited_baselines" in metrics:
        exact = metrics["audited_baselines"]["exact_alias_first"]
        lines.append(
            f"- 纯名称/别名精确匹配基线（多匹配取首项、无匹配 NIL）：{exact['correct']}/{exact['total']}，{pct(exact['accuracy'])}。"
        )
    if "sensitivity" in metrics:
        sensitivity = metrics["sensitivity"]
        remaining = sensitivity["remaining"]
        lines.extend(
            [
                f"- 敏感性分析：按推理前审计固定剔除 {sensitivity['excluded_count']} 个有问题的提及后，"
                f"{remaining['correct']}/{remaining['total']}，{pct(remaining['accuracy'])}。主成绩仍为原始全部样本成绩。",
                "  剔除集合为上下文不足的30个提及、公司/品牌策略冲突的15个品牌提及、1个演唱会错标；"
                "集合不依据模型预测是否正确决定。详见 independent_dataset_audit.json。",
            ]
        )
    lines += ["", "## 错误样例", ""]
    examples, category_counts = [], Counter()
    for row in predictions:
        if not row["correct"] and category_counts[row["category"]] < 2:
            examples.append(row)
            category_counts[row["category"]] += 1
    for row in examples:
        reasons = "; ".join(
            f"{c['candidate_id']}: {c['verdict']} — "
            f"{c['decision'].get('model_reason', c['decision']['reason'])[:180] if c['decision'] else 'missing'}"
            for c in row["comparisons"]
        )
        lines.extend(
            [
                f"- `{row['sample_id']}` 第 {row['mention_index'] + 1} 个提及 **{row['mention']}**："
                f"gold=`{row['gold']}`，pred=`{row['prediction'] or '未决'}`（{row['status']}）。",
                f"  原文：{row['text']}",
                f"  判定：{reasons}",
            ]
        )
    lines += [
        "",
        "报告中的长理由截取前180字；完整逐提及预测、理由和引用见 predictions.jsonl；全部错例见 errors.jsonl；"
        "原始 pair 决策见 decisions.jsonl。配置、代码快照与文件哈希保存在本目录。",
        "",
        "## 复现",
        "",
        "在项目根目录使用现有 .env 模型配置。新推理需要一个尚不存在的输出目录：",
        "",
        "```bash",
        ".venv/bin/python scripts/evaluate_disambiguation.py \\",
        "  --dataset /Users/rian/Documents/graphrag_entity_disambiguation.jsonl \\",
        "  --output outputs/disambiguation-new-run --concurrency 8",
        ".venv/bin/python scripts/audit_disambiguation_dataset.py \\",
        "  --dataset /Users/rian/Documents/graphrag_entity_disambiguation.jsonl \\",
        "  --output-json outputs/disambiguation-new-run/independent_dataset_audit.json",
        ".venv/bin/python scripts/evaluate_disambiguation.py \\",
        "  --dataset /Users/rian/Documents/graphrag_entity_disambiguation.jsonl \\",
        "  --output outputs/disambiguation-new-run --rescore",
        "```",
        "",
        "--rescore 仅用已保存的决策重新计算报告，不再次调用模型。",
        "",
    ]
    return "\n".join(lines)


def finalize(output, tasks, decisions, audit, manifest):
    predictions, metrics = score(tasks, decisions)
    independent_path = output / "independent_dataset_audit.json"
    if independent_path.exists():
        independent = json.loads(independent_path.read_text(encoding="utf-8"))
        if independent["sha256"] != manifest["dataset_sha256"]:
            raise ValueError("Independent audit belongs to another dataset")
        metrics["audited_baselines"] = {
            key: {field: value[field] for field in ("correct", "total", "accuracy")}
            for key, value in independent["baselines"].items()
        }
        exclusions = {
            (item["sample_id"], item["mention_index"])
            for item in independent["sensitivity_exclusions"]
        }
        excluded = [
            row for row in predictions if (row["sample_id"], row["mention_index"]) in exclusions
        ]
        remaining = [
            row for row in predictions if (row["sample_id"], row["mention_index"]) not in exclusions
        ]
        metrics["sensitivity"] = {
            "excluded_count": len(excluded),
            "excluded": metric(excluded),
            "remaining": metric(remaining),
        }
    baseline_dir = manifest.get("baseline_dir")
    if baseline_dir:
        baseline_dir = Path(baseline_dir)
        old_manifest = json.loads((baseline_dir / "manifest.json").read_text(encoding="utf-8"))
        old = json.loads((baseline_dir / "metrics.json").read_text(encoding="utf-8"))
        if old_manifest["dataset_sha256"] != manifest["dataset_sha256"]:
            raise ValueError("Baseline dataset changed")
        if (old_manifest["provider"], old_manifest["model"]) != (
            manifest["provider"],
            manifest["model"],
        ):
            raise ValueError("Baseline model changed")
        metrics["comparison"] = {
            "baseline_dir": str(baseline_dir),
            "baseline_overall": old["overall"],
            "accuracy_delta": metrics["overall"]["accuracy"] - old["overall"]["accuracy"],
            "baseline_by_category": old["by_category"],
            "baseline_pair_errors": old["pair_errors"],
        }
    write_json(output / "metrics.json", metrics)
    for name, items in (
        ("predictions.jsonl", predictions),
        ("errors.jsonl", [p for p in predictions if not p["correct"]]),
    ):
        (output / name).write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items), encoding="utf-8"
        )
    (output / "report.md").write_text(
        render_report(metrics, audit, manifest, predictions), encoding="utf-8"
    )
    return metrics


async def run(args):
    raw = args.dataset.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate sample IDs")
    if not rows:
        raise ValueError("Empty dataset")
    corpus, candidate_data, tasks, audit = prepare(rows, legacy_input=args.legacy_input)
    if args.rescore:
        manifest = json.loads((args.output / "manifest.json").read_text(encoding="utf-8"))
        if (
            manifest["dataset_sha256"] != hashlib.sha256(raw).hexdigest()
            or manifest["corpus_sha256"] != corpus.sha256
        ):
            raise ValueError(
                "Dataset or inference adapter changed; cannot rescore these predictions"
            )
        if "finished_at" not in manifest:
            raise ValueError("Inference is not finished")
        decisions = [
            json.loads(line)
            for line in (args.output / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        expected = {
            tuple(sorted((left, right)))
            for left, rights in candidate_data.items()
            for right in rights
        }
        actual = {tuple(sorted((d["left"], d["right"]))) for d in decisions}
        if actual != expected or len(decisions) != len(expected):
            raise ValueError("Missing, duplicate or unexpected pair decisions")
        metrics = finalize(args.output, tasks, decisions, audit, manifest)
        source = Path(__file__).resolve()
        shutil.copyfile(source, args.output / "report_script.py")
        manifest["reporting_code_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest["rescored_at"] = datetime.now(UTC).isoformat()
        write_json(args.output / "manifest.json", manifest)
        print(
            json.dumps(
                {"output": str(args.output), "overall": metrics["overall"]}, ensure_ascii=False
            ),
            flush=True,
        )
        return
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(args.dataset, args.output / "dataset.jsonl")
    write_json(args.output / "corpus.json", corpus.to_dict())
    write_json(args.output / "scoring_sidecar.json", tasks)
    write_json(args.output / "dataset_audit.json", audit)
    if args.prepare_only:
        print(
            json.dumps({k: v for k, v in audit.items() if k != "repairs"}, ensure_ascii=False),
            flush=True,
        )
        return
    settings = Settings()
    if not JsonModel(settings).configured:
        raise ValueError("Project model is not configured")
    config = ResolverConfig(
        retrieval_policy="balanced",
        candidate_limit=10,
        concurrency=args.concurrency,
        model_policy="review",
        max_model_calls=2000,
        timeout_seconds=60,
    )
    manifest = {
        "started_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "corpus_sha256": corpus.sha256,
        "provider": settings.model_provider,
        "model": settings.model_name or "qwen3.7-plus",
        "temperature": 0,
        "max_tokens": 800,
        "config": asdict(config),
        "transport_timeout": settings.model_timeout_seconds,
        "transport_max_retries": settings.model_max_retries,
        "protocol": "given-candidates-existing-pair-judge-v1"
        if args.legacy_input
        else "given-candidates-located-catalog-v3",
        "code_sha256": {},
        "input_contract": "legacy" if args.legacy_input else "located-catalog-v2",
        "baseline_dir": str(args.baseline_dir.resolve()) if args.baseline_dir else None,
        "cache_path": str(args.cache.resolve()) if args.cache else None,
    }
    root = Path(__file__).resolve().parents[1]
    sources = [
        Path(__file__).resolve(),
        root / "src/bank_project/resolution/service.py",
        root / "src/bank_project/alignment/model_client.py",
        root / "src/bank_project/settings.py",
        *sorted((root / "src/bank_project/resolution/engine").glob("*.py")),
    ]
    for source in sources:
        rel = source.relative_to(root)
        target = args.output / "code_snapshot" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        manifest["code_sha256"][str(rel)] = hashlib.sha256(source.read_bytes()).hexdigest()
    started = time.monotonic()
    last_progress = 0.0
    with (args.output / "decisions.jsonl").open("w", encoding="utf-8") as stream:

        async def on_decision(decision):
            stream.write(json.dumps(decision, ensure_ascii=False) + "\n")
            stream.flush()

        async def on_progress(completed, total, failed, skipped):
            nonlocal last_progress
            now = time.monotonic()
            if completed in (0, 1, total) or now - last_progress >= 10:
                last_progress = now
                progress = {
                    "completed": completed,
                    "total": total,
                    "failed": failed,
                    "skipped": skipped,
                    "elapsed_seconds": round(now - started, 1),
                }
                print(json.dumps(progress), flush=True)
                write_json(args.output / "progress.json", progress)

        async with httpx.AsyncClient(
            timeout=settings.model_timeout_seconds,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=args.concurrency, max_keepalive_connections=args.concurrency
            ),
        ) as client:
            judge = SourceModelJudge(
                CompletionAdapter(JsonModel(settings, client=client)),
                version=f"{settings.model_provider}/{settings.model_name or 'default'}:{JUDGE_REVISION}:{digest(str(settings.model_base_url))}",
                namespace=corpus.namespace,
                concise_quotes=True,
                cache_path=args.cache.resolve()
                if args.cache
                else args.output / "judge_cache.sqlite3",
            )
            manifest["judge_version"] = judge.version
            (args.output / "system_prompt.txt").write_text(judge.prompt, encoding="utf-8")
            from bank_project.resolution.engine.catalog import CATALOG_PROMPT

            (args.output / "catalog_system_prompt.txt").write_text(CATALOG_PROMPT, encoding="utf-8")
            write_json(args.output / "manifest.json", manifest)
            try:
                resolution = await resolve_evidence(
                    corpus,
                    config,
                    judge=judge,
                    candidate_data=(candidate_data, set()),
                    on_progress=on_progress,
                    on_decision=on_decision,
                )
            finally:
                judge.close()
    manifest["elapsed_seconds"] = round(time.monotonic() - started, 3)
    manifest["finished_at"] = datetime.now(UTC).isoformat()
    manifest["diagnostics"] = resolution.diagnostics
    write_json(args.output / "manifest.json", manifest)
    metrics = finalize(args.output, tasks, resolution.decisions, audit, manifest)
    print(
        json.dumps({"output": str(args.output), "overall": metrics["overall"]}, ensure_ascii=False),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=8, choices=range(1, 33))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="Recompute reports from a finished run without model calls",
    )
    parser.add_argument(
        "--legacy-input",
        action="store_true",
        help="Use the original untyped input adapter (for historical scoring)",
    )
    parser.add_argument(
        "--baseline-dir",
        type=Path,
        help="Completed same-dataset, same-model baseline for paired reporting",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        help="Reuse content-addressed validated model results from a local cache",
    )
    args = parser.parse_args()
    args.dataset, args.output = args.dataset.resolve(), args.output.resolve()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
