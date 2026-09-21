"""Run a small synthetic boundary smoke test, never a business-accuracy estimate.

Fixed invented cases and expected verdicts are saved before any provider call.
Only ordinary Mention records enter the shared production resolver/judge.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from bank_project.alignment.model_client import JsonModel
from bank_project.resolution.engine.contracts import Corpus, Mention, ResolverConfig, digest
from bank_project.resolution.engine.model_judge import JUDGE_REVISION
from bank_project.resolution.engine.resolver import resolve_evidence
from bank_project.resolution.engine.runner import write_json
from bank_project.resolution.service import CompletionAdapter, SourceModelJudge
from bank_project.settings import Settings


def source(name, context, *, occurrence=0, kind=""):
    """Locate one explicit synthetic occurrence without using its expected verdict."""
    start = -1
    for _ in range(occurrence + 1):
        start = context.index(name, start + 1)
    return {
        "name": name,
        "type": kind,
        "context": context,
        "source_span": [start, start + len(name)],
        "evidence_kind": "source",
    }


def directory(name, kind, aliases):
    return {
        "name": name,
        "type": kind,
        "aliases": aliases,
        "evidence_kind": "catalog",
        "context": "候选目录记录："
        + json.dumps({"name": name, "type": kind, "aliases": aliases}, ensure_ascii=False),
    }


def fixed_cases():
    """Predeclared invented examples; neither user dataset nor model output is read."""
    cases = [
        (
            "同名人员与常见职业不足以合并",
            "uncertain",
            source("程砚", "程砚是一名后端开发工程师，负责维护业务接口。", kind="Person"),
            source("程砚", "程砚从事后端开发工作，经常参加项目评审。", kind="Person"),
        ),
        (
            "同名公司与相同行业不足以合并",
            "uncertain",
            source("远汀科技", "远汀科技为企业提供云软件服务。", kind="Organization"),
            source("远汀科技", "远汀科技经营企业云软件业务。", kind="Organization"),
        ),
        (
            "来源明确笔名对应",
            "same",
            source("许沅", "许沅的笔名是许澜，她以此署名发表作品。", kind="Person"),
            source("许澜", "许澜发表了一篇散文。", kind="Person"),
        ),
        (
            "相同来源登记编号",
            "same",
            source("星衡研究院", "星衡研究院的机构登记编号为 ZH-6409。", kind="Organization"),
            source(
                "星衡研究院",
                "星衡研究院提交了年度报告，机构登记编号为 ZH-6409。",
                kind="Organization",
            ),
        ),
        (
            "同一单位中不同员工编号",
            "different",
            source(
                "程砚", "北礁实验室的人事档案记载，程砚的唯一员工编号为 CY-204。", kind="Person"
            ),
            source(
                "程砚", "北礁实验室的人事档案记载，程砚的唯一员工编号为 CY-879。", kind="Person"
            ),
        ),
        (
            "公司与其持有品牌保持不同身份",
            "different",
            source("露畴", "露畴是一家已登记的公司，主营家居用品。", kind="Organization"),
            source("露畴", "露畴是一个家居品牌，该商标由露畴公司持有。", kind="Brand"),
        ),
    ]
    dual = "榆星公司发布了新终端，果盘里的榆星是本地培育的葡萄品种。"
    company = directory("榆星公司", "Organization", ["榆星"])
    fruit = directory("榆星葡萄", "Food", ["榆星"])
    cases.extend(
        [
            ("第一处同名公司匹配公司目录", "same", source("榆星", dual), company),
            ("第一处同名公司排除食品目录", "different", source("榆星", dual), fruit),
            (
                "第二处同名食品排除公司目录",
                "different",
                source("榆星", dual, occurrence=1),
                company,
            ),
            ("第二处同名食品匹配食品目录", "same", source("榆星", dual, occurrence=1), fruit),
        ]
    )
    dual = "工程师陶溪主持了设计评审，村庄陶溪今天发布了道路维修公告。"
    cases.extend(
        [
            (
                "第一处同名人物匹配人物目录",
                "same",
                source("陶溪", dual),
                directory("陶溪", "Person", []),
            ),
            (
                "第二处同名村庄匹配地点目录",
                "same",
                source("陶溪", dual, occurrence=1),
                directory("陶溪村", "Location", ["陶溪"]),
            ),
        ]
    )
    return [
        {
            "case_id": f"case-{index:02d}",
            "description": description,
            "expected": expected,
            "left": left,
            "right": right,
        }
        for index, (description, expected, left, right) in enumerate(cases)
    ]


def build_corpus(cases):
    """Keep expectation, descriptive test names and case IDs out of judge inputs."""
    mentions, pairs, lookup = [], {}, {}
    for index, case in enumerate(cases):
        mids = []
        for side_index, side in enumerate(("left", "right")):
            row = dict(case[side])
            if "source_span" in row:
                row["source_span"] = tuple(row["source_span"])
            if "aliases" in row:
                row["aliases"] = tuple(row["aliases"])
            mid = f"m{index:02d}_{side_index}"
            mentions.append(Mention(mention_id=mid, source_id=f"s{index:02d}_{side_index}", **row))
            mids.append(mid)
        pairs[mids[0]], pairs[mids[1]] = [mids[1]], [mids[0]]
        lookup[tuple(mids)] = case
    return Corpus("synthetic-boundaries-v1", tuple(mentions)), pairs, lookup


async def run(args):
    cases = fixed_cases()
    case_path = args.output.with_name(args.output.stem + ".cases.json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if case_path.exists():
        frozen = json.loads(case_path.read_text(encoding="utf-8"))
        if frozen["cases"] != cases:
            raise ValueError("Fixed cases changed after being saved")
    else:
        frozen = {
            "prepared_at": datetime.now(UTC).isoformat(),
            "note": "人工预先固定的虚构边界烟测，不读取用户数据集；预期结果不进入模型输入。",
            "cases_sha256": digest(cases),
            "cases": cases,
        }
        write_json(case_path, frozen)
    if args.prepare_only:
        print(json.dumps({"prepared_cases": len(cases), "path": str(case_path)}), flush=True)
        return
    if args.output.exists():
        raise ValueError("Output already exists; retain the original smoke-test result")
    corpus, pairs, lookup = build_corpus(cases)
    settings = Settings()
    if not JsonModel(settings).configured:
        raise ValueError("Project model is not configured")
    config = ResolverConfig(
        concurrency=2, model_policy="review", max_model_calls=len(cases), timeout_seconds=60
    )
    started = time.monotonic()
    async with httpx.AsyncClient(
        timeout=settings.model_timeout_seconds,
        follow_redirects=False,
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
    ) as client:
        judge = SourceModelJudge(
            CompletionAdapter(JsonModel(settings, client=client)),
            version=f"{settings.model_provider}/{settings.model_name or 'default'}:{JUDGE_REVISION}",
            namespace=corpus.namespace,
            concise_quotes=True,
        )

        async def progress(completed, total, failed, skipped):
            print(
                json.dumps(
                    {"completed": completed, "total": total, "failed": failed, "skipped": skipped}
                ),
                flush=True,
            )

        try:
            resolution = await resolve_evidence(
                corpus,
                config,
                judge=judge,
                candidate_data=(pairs, set()),
                on_progress=progress,
            )
        finally:
            judge.close()
    rows = []
    for decision in resolution.decisions:
        case = lookup[decision["left"], decision["right"]]
        actual = decision.get("proposal") or decision["verdict"]
        rows.append(
            {
                **case,
                "actual": actual,
                "passed": actual == case["expected"] and decision["origin"] != "error",
                "decision": decision,
            }
        )
    result = {
        "note": "仅为人工合成边界烟测，不能视为独立业务准确率；模型建议不自动合并。",
        "cases_path": str(case_path),
        "cases_sha256": frozen["cases_sha256"],
        "cases_prepared_at": frozen["prepared_at"],
        "finished_at": datetime.now(UTC).isoformat(),
        "model": settings.model_name,
        "provider": settings.model_provider,
        "judge_revision": JUDGE_REVISION,
        "config": asdict(config),
        "corpus_sha256": corpus.sha256,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "total": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "overmerge_proposals": [
            row["case_id"] for row in rows if row["actual"] == "same" and row["expected"] != "same"
        ],
        "quote_or_provider_failures": sum(row["decision"]["origin"] == "error" for row in rows),
        "accepted_merges": sum(row["decision"]["accepted"] for row in rows),
        "results": rows,
    }
    write_json(args.output, result)
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "total",
                    "passed",
                    "overmerge_proposals",
                    "quote_or_provider_failures",
                    "accepted_merges",
                )
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    args.output = args.output.resolve()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
