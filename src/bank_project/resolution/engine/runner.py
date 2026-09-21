# Copyright (c) 2026 Microsoft Corporation.
# Licensed under the MIT License
# ruff: noqa: RUF001
# Chinese report text intentionally uses Chinese punctuation.

"""Write isolated, immutable paired experiments and honest comparison reports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from bank_project.resolution.engine.contracts import Corpus, ResolverConfig, digest, require
from bank_project.resolution.engine.evaluation import (
    annotation_template,
    differences,
    evaluate,
    validate_gold,
)
from bank_project.resolution.engine.legacy import resolve_legacy
from bank_project.resolution.engine.resolver import IdentityJudge, resolve_evidence
from bank_project.resolution.engine.synonyms import (
    AliasJudge,
    expand_corpus,
    synonym_proposals,
    validate_dictionary,
)


def write_json(path: Path, value) -> None:
    """Atomically replace one artifact; callers own an exclusive run directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def map_relations(corpus: Corpus, resolution) -> None:
    """Export ID-based edges without guessing unresolved endpoints."""
    mapping = {item["mention_id"]: item for item in resolution.memberships}
    for relation in corpus.relations:
        source, target = (
            mapping[relation[key]] for key in ("source_mention_id", "target_mention_id")
        )
        resolution.relations.append(
            {
                **relation,
                "source_entity_id": source["entity_id"],
                "target_entity_id": target["entity_id"],
                "status": "dropped"
                if not source["entity_id"] or not target["entity_id"]
                else "provisional"
                if "provisional" in (source["status"], target["status"])
                else "resolved",
            }
        )


def markdown_report(report: dict) -> str:
    """Present measured estimates separately from unmeasured quality targets."""
    baseline, challenger = report.get("methods", ["legacy_title_v1", "evidence_v1"])
    lines = [
        "# 实体消歧方法对比",
        "",
        f"- 输入指纹：`{report['corpus_sha256']}`",
        f"- 评测状态：**{report['evaluation_status']}**",
        f"- 输入单位：`{report['input_kind']}`；记录数：{report['records']}",
        f"- 归属发生变化的记录：{report['changed_records']}",
        f"- 对比方法：`{baseline}` → `{challenger}`",
        f"- 新方法模型策略：`{report['diagnostics']['config']['model_policy']}`；判定请求数：{report['diagnostics']['judge_requests']}",
        "- 两种方法使用相同输入；默认索引没有被替换。",
        "",
    ]
    if report["declared_synthetic"]:
        lines += [
            "**合成示例数据：本报告仅验证实现与指标计算，分数不代表真实业务准确率。**",
            "",
        ]
    if report.get("reference_label_provenance"):
        lines += [f"参考标签来源：{report['reference_label_provenance']}", ""]
    if report["evaluation_status"] == "NOT_MEASURED_NO_GOLD":
        lines += [
            "尚无人工金标，不能据此判断哪种方法准确率更高。请填写 annotation_template.json 后重新运行。",
            "",
        ]
    else:
        supports = report["metrics"][challenger]
        lines += [
            f"可确认身份的金标记录：{supports['evaluated_mentions']}；证据不足的金标记录：{supports['insufficient_mentions']}。",
            "聚类指标仅使用可确认身份的记录；证据不足的记录单列强判率，不假设其真实身份。",
            "",
            f"| 指标 | {baseline} | {challenger} |",
            "|---|---:|---:|",
        ]

        def value(method, section, key):
            item = report["metrics"][method][section][key]
            item = item["value"] if isinstance(item, dict) else item
            return "N/A" if item is None else f"{item:.2%}"

        for label, section, key in (
            ("Pairwise precision", "pairwise", "precision"),
            ("Pairwise recall", "pairwise", "recall"),
            ("Pairwise F1", "pairwise", "f1"),
            ("B³ precision", "b_cubed", "precision"),
            ("B³ recall", "b_cubed", "recall"),
            ("B³ F1", "b_cubed", "f1"),
        ):
            lines.append(
                f"| {label} | {value(baseline, section, key)} | {value(challenger, section, key)} |"
            )

        def supported_value(method, key):
            item = report["metrics"][method][key]
            if item is None:
                return "N/A"
            if not isinstance(item, dict):
                return str(item)
            support = f"{item['numerator']}/{item['denominator']}"
            return (
                f"N/A ({support})" if item["value"] is None else f"{item['value']:.2%} ({support})"
            )

        for label, key in (
            ("污染簇数量", "contaminated_clusters"),
            ("污染簇内记录比例", "mentions_in_contaminated_clusters"),
            ("记录保留率", "retained_record_coverage"),
            ("待定比例", "provisional_fraction"),
            ("证据不足被强判比例", "insufficient_forced_resolution"),
            ("本批候选召回率", "candidate_recall"),
            ("关系端点簇身份正确率", "endpoint_cluster_identity_accuracy"),
        ):
            lines.append(
                f"| {label} | {supported_value(baseline, key)} | {supported_value(challenger, key)} |"
            )
        lines += [
            "",
            "括号中为分子/分母；N/A 表示无适用样本或该方法无此指标。待定记录作为单独实体参与聚类评分。",
            "以上是该金标集上的点估计，不是总体准确率保证；置信区间和正式发布验收需独立审计。",
            "",
        ]
    lines += [
        "## 边界",
        "",
        "- 原始标准抽取导出的单位是 chunk 内实体记录，无法恢复抽取时已混合的同名对象。",
        "- provisional 仍参与聚类评分；不把待定或丢失记录从分母中移除。",
        "- 规则和模型仅读取 corpus；金标只用于评分。人为约束和标识符是否泄漏测试身份仍需数据审计。",
        "- 新方法输出为实验实体/关系表，尚未作为生产查询 schema v2 发布。",
        f"- 逐条差异见 differences.jsonl；候选、证据和拒绝合并原因见 {challenger}/result.json。",
        "",
    ]
    return "\n".join(lines)


async def compare_methods(
    corpus: Corpus,
    output: Path,
    *,
    config: ResolverConfig | None = None,
    gold: dict | None = None,
    judge: IdentityJudge | None = None,
    vectors: dict | None = None,
    method: str = "evidence_v1",
    synonyms: dict | None = None,
    max_alias_calls: int = 200,
    alias_progress=None,
    pair_progress=None,
    on_decision=None,
) -> dict:
    """Run both methods once and prevent overwriting any previous experiment."""
    require(method in ("evidence_v1", "synonym_llm_v1"), "Unknown comparison method")
    config = config or ResolverConfig(
        model_policy="apply" if method == "synonym_llm_v1" else "review"
    )
    if method == "synonym_llm_v1":
        require(
            judge is not None and callable(getattr(judge, "expand_aliases", None)),
            "synonym_llm_v1 requires a model judge with alias expansion",
        )
        validate_dictionary(synonyms)
        require(
            type(max_alias_calls) is int and max_alias_calls >= 0,
            "max_alias_calls must be nonnegative",
        )
    else:
        require(synonyms is None, "Synonyms require synonym_llm_v1")
    labels = validate_gold(corpus, gold) if gold is not None else None
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": "er-run-v1",
        "state": "running",
        "methods": ["legacy_title_v1", method],
        "created_at": datetime.now(UTC).isoformat(),
        "corpus_sha256": corpus.sha256,
        "config_sha256": digest(asdict(config)),
        "gold_sha256": digest(gold) if gold is not None else None,
        "judge_version": judge.version if judge else None,
        "vectors_sha256": digest(vectors) if vectors is not None else None,
        "synonyms_sha256": digest(synonyms) if synonyms is not None else None,
        "max_alias_calls": max_alias_calls if method == "synonym_llm_v1" else 0,
        "implementation_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(__file__).parent.glob("*.py"))
        },
    }
    write_json(output / "run.json", manifest)
    implementation = output / "implementation"
    implementation.mkdir()
    for source in sorted(Path(__file__).parent.glob("*.py")):
        (implementation / f"{source.name}.snapshot").write_bytes(source.read_bytes())
    write_json(output / "corpus.json", corpus.to_dict())
    write_json(output / "resolver_config.json", asdict(config))
    write_json(output / "annotation_template.json", annotation_template(corpus))
    if gold is not None:
        write_json(output / "gold.json", gold)
    if vectors is not None:
        write_json(output / "vectors.json", vectors)
    if synonyms is not None:
        write_json(output / "synonyms.input.json", synonyms)
    try:
        legacy = resolve_legacy(corpus)
        map_relations(corpus, legacy)
        write_json(output / "legacy_title_v1" / "result.json", asdict(legacy))
        retrieval_corpus = None
        expansion = None
        if method == "synonym_llm_v1":
            retrieval_corpus, expansion = await expand_corpus(
                corpus,
                cast("AliasJudge", judge),
                config,
                synonyms,
                max_alias_calls=max_alias_calls,
                on_progress=alias_progress,
            )
            write_json(output / "alias_expansion.json", expansion)
        evidence = await resolve_evidence(
            corpus,
            config,
            judge,
            vectors,
            retrieval_corpus=retrieval_corpus,
            method=method,
            model_only=method == "synonym_llm_v1",
            on_progress=pair_progress,
            on_decision=on_decision,
        )
        if expansion is not None:
            evidence.diagnostics["alias_model_requests"] = expansion["model_requests"]
            evidence.diagnostics["expanded_records"] = sum(
                bool(row["aliases"]) for row in expansion["records"]
            )
            evidence.diagnostics["alias_failures"] = sum(
                row["state"].startswith("ALIAS_FAILED") for row in expansion["records"]
            )
            write_json(output / "synonyms.proposals.json", synonym_proposals(corpus, evidence))
        map_relations(corpus, evidence)
        write_json(output / method / "result.json", asdict(evidence))
        changed = differences(corpus, legacy, evidence)
        (output / "differences.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in changed),
            encoding="utf-8",
        )
        report = {
            "schema_version": "er-comparison-v1",
            "methods": ["legacy_title_v1", method],
            "reference_label_provenance": gold.get("label_provenance") if gold else None,
            "corpus_sha256": corpus.sha256,
            "input_kind": corpus.input_kind,
            "declared_synthetic": corpus.metadata.get("synthetic") is True,
            "records": len(corpus.mentions),
            "evaluation_status": "MEASURED_ON_PROVIDED_GOLD"
            if labels is not None
            else "NOT_MEASURED_NO_GOLD",
            "changed_records": len(changed),
            "entity_counts": {
                "legacy_title_v1": len(legacy.entities),
                method: len(evidence.entities),
            },
            "metrics": {
                result.method: evaluate(corpus, result, labels) for result in (legacy, evidence)
            }
            if labels is not None
            else None,
            "diagnostics": evidence.diagnostics,
        }
        write_json(output / "comparison.json", report)
        (output / "comparison.md").write_text(markdown_report(report), encoding="utf-8")
        manifest["state"] = "complete"
        manifest["artifact_sha256"] = {
            str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(output.rglob("*"))
            if path.is_file() and path.name != "run.json"
        }
        write_json(output / "run.json", manifest)
    except BaseException as exc:
        manifest["state"] = "failed"
        manifest["error_type"] = type(exc).__name__
        write_json(output / "run.json", manifest)
        raise
    else:
        return report
