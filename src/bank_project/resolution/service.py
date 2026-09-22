"""Complete graph resolution using the migrated GraphRAG evidence engine."""

import asyncio
import inspect
import json
import logging
import sqlite3
import time
from contextlib import AsyncExitStack, suppress
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import httpx

from bank_project.alignment.model_client import JsonModel
from bank_project.alignment.models import AlignmentError

from .adapter import (
    MISSING_SOURCE_CONTEXT,
    annotate_run,
    candidate_from_decision,
    corpus_from_graph,
    model_recommends_merge,
    validate_graph,
)
from .engine.contracts import CandidateBudgetExceeded, Corpus, ResolverConfig, digest
from .engine.model_judge import JUDGE_REVISION, LLMJudge
from .engine.runner import compare_methods
from .experiments import load_experiment
from .models import Audit
from .projection import update_run
from .store import ResolutionStore, now

logger = logging.getLogger(__name__)


class CompletionAdapter:
    """Use the project's bounded model transport with the original evidence prompts."""

    def __init__(self, model):
        self.model = model

    async def completion_async(self, *, messages, **kwargs):
        options = {}
        if isinstance(self.model, JsonModel):
            options["max_tokens"] = kwargs.get("max_completion_tokens")
        value = await self.model.complete(messages[0]["content"], messages[1]["content"], **options)
        return SimpleNamespace(content=json.dumps(value, ensure_ascii=False))


class SourceModelJudge(LLMJudge):
    """Missing document text cannot be replaced with an extracted node description."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prompt += (
            "\nsource 记录的 context 是可引用的来源证据。source 的 name、description、aliases "
            "可能由抽取模型生成，只用于定位，不能代替原文证明两个来源记录的身份。"
            "显式标为 catalog 的记录则表示外部候选目录，其原始 context 中的名称、类型、别名可用于候选词义匹配；"
            "不要将普通来源记录当作目录。提供 source_span 时，只判断该处提及。"
            "通过各侧 evidence_options 的 ID 选择引用，系统将回填并校验原文；证据不足返回 uncertain。"
        )

    async def judge(self, left, right):
        if MISSING_SOURCE_CONTEXT in (left.context, right.context):
            return {"verdict": "uncertain", "reason": "缺少文档原文，未生成合并建议"}
        return await super().judge(left, right)

    async def expand_aliases(self, mention):
        if mention.context == MISSING_SOURCE_CONTEXT:
            return {"aliases": []}
        return await super().expand_aliases(mention)


class ResolutionService:
    def __init__(self, settings, graph_loader):
        self.settings, self.graph_loader = settings, graph_loader
        self.store = ResolutionStore(settings.data_dir / "resolution" / "runs.sqlite3")
        self.tasks = set()
        self.model = JsonModel(settings)
        self.experiments_root = (
            settings.resolution_experiments_dir or settings.data_dir / "resolution" / "experiments"
        )

    async def start(self, request):
        run, owner = await asyncio.to_thread(
            self.store.create, request.source_kind, request.source_id, request.options
        )
        task = asyncio.create_task(self._execute(run, owner))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return run

    async def create(self, request):
        return await self.start(request)

    def list(self):
        return self.store.list()

    def get(self, run_id):
        return self.store.get(run_id)

    def decide(self, run_id, request):
        return annotate_run(self.store.decide(run_id, request))

    def manual(self, run_id, request):
        return annotate_run(self.store.decide(run_id, request, manual=True))

    def graph(self, run_id):
        return self.store.graph(run_id)

    async def _heartbeat(self, run_id, owner, report):
        while True:
            await asyncio.sleep(10)
            await asyncio.to_thread(self.store.heartbeat, run_id, owner)
            await report()

    async def _execute(self, run, owner):
        started = time.monotonic()
        progress_lock = asyncio.Lock()

        async def report(message=None):
            async with progress_lock:
                if message is not None:
                    run.progress = message
                elapsed = int(time.monotonic() - started)
                await asyncio.to_thread(
                    self.store.progress,
                    run.id,
                    owner,
                    f"{run.progress}；已用时 {elapsed // 60}分{elapsed % 60:02d}秒",
                    run,
                )

        heartbeat = asyncio.create_task(self._heartbeat(run.id, owner, report))
        try:
            raw = await asyncio.to_thread(self.graph_loader, run.source_kind, run.source_id)
            if inspect.isawaitable(raw):
                raw = await raw
            graph = await asyncio.to_thread(validate_graph, raw, run.source_kind, run.source_id)
            run.name = graph.get("name") or run.name
            run.summary.original_node_count = run.summary.node_count = len(graph["nodes"])
            run.summary.original_edge_count = run.summary.edge_count = len(graph["edges"])
            await report(
                f"已读取完整图谱：{len(graph['nodes'])} 节点 / {len(graph['edges'])} 关系；正在整理证据"
            )
            run.candidates, run.diagnostics = await self._analyze(run, graph, report)
            if heartbeat.done():
                heartbeat.result()
            run.diagnostics["elapsed_seconds"] = round(time.monotonic() - started, 2)
            run.status, run.error = "ready", None
            update_run(run, graph)
            annotate_run(run)
            await asyncio.to_thread(self.store.finish, run, owner, graph)
        except asyncio.CancelledError:
            run.status, run.error, run.progress = (
                "failed",
                "服务停止导致分析中断，请重新创建消歧任务；原图未修改",
                "分析中断",
            )
            with suppress(AlignmentError, sqlite3.Error, OSError):
                await asyncio.to_thread(self.store.finish, run, owner)
            raise
        except Exception as exc:
            run.status, run.progress = "failed", "分析未完成"
            if isinstance(exc, AlignmentError):
                run.error = exc.message
            elif isinstance(exc, CandidateBudgetExceeded):
                run.error = str(exc)
                run.diagnostics["candidate_budget"] = {
                    "selected_pairs_at_least": exc.selected_pairs,
                    "max_pairs": exc.max_pairs,
                    "complete": False,
                }
            elif isinstance(exc, ValueError):
                # Engine validation errors contain no provider response or credentials.
                run.error = f"消歧输入或候选预算校验失败：{exc}"
            else:
                run.error = "消歧分析未完成，请检查图谱或模型服务后重试；原图未修改"
                logger.warning("Resolution failed (%s)", type(exc).__name__)
            with suppress(AlignmentError, sqlite3.Error, OSError):
                await asyncio.to_thread(self.store.finish, run, owner)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, AlignmentError, sqlite3.Error, OSError):
                await heartbeat

    async def _analyze(self, run, graph, report):
        corpus = (
            Corpus.from_dict(graph["resolution_corpus"])
            if graph.get("resolution_corpus") and graph["source_id"].startswith("extracted:")
            else await asyncio.to_thread(corpus_from_graph, graph)
        )
        options = run.options
        config = ResolverConfig(
            **options.model_dump(
                exclude={"method", "synonyms", "vectors", "gold", "max_alias_calls"}
            )
        )

        async def alias_progress(completed, total, failed, skipped):
            await report(
                f"步骤 1/3 · 别名检查 {completed}/{total} 个节点"
                f"（失败 {failed}，预算跳过 {skipped}）；模型并发 {config.concurrency}"
            )

        preview = {}
        by_id = {node["id"]: node for node in graph["nodes"]}
        source_mentions = {m.mention_id: m for m in corpus.mentions}
        last_preview = 0.0
        pair_started = None

        async def on_decision(decision):
            preview[(decision["left"], decision["right"])] = decision

        async def pair_progress(completed, total, failed, skipped):
            nonlocal last_preview, pair_started
            current = time.monotonic()
            if pair_started is None:
                pair_started = current
            if completed not in (0, 1, total) and current - last_preview < 2:
                return
            last_preview = current
            active_completed = completed - skipped
            planned = min(total, config.max_model_calls)
            eta = (
                max(0, planned - active_completed) * (current - pair_started) / active_completed
                if active_completed >= config.concurrency
                else None
            )
            # Read-only preview is deliberately bounded; all results are saved at finish.
            decisions = sorted(preview.values(), key=lambda d: not model_recommends_merge(d))[:50]
            run.candidates = [
                candidate_from_decision(d, by_id, source_mentions, run.source_kind)
                for d in decisions
            ]
            run.summary.excluded_count = sum(
                bool(d.get("identity_guard", {}).get("block_merge")) for d in preview.values()
            )
            run.summary.pending_count = sum(model_recommends_merge(d) for d in preview.values())
            run.summary.not_recommended_count = (
                completed - run.summary.excluded_count - run.summary.pending_count
            )
            run.diagnostics["analysis"] = {
                "completed": completed,
                "total": total,
                "failed": failed,
                "skipped": skipped,
                "eta_seconds": round(eta) if eta is not None else None,
                "preview_limit": 50,
                "partial": True,
                "model_budget": config.max_model_calls,
            }
            message = f"候选比较 {completed}/{total} 对（失败 {failed}，预算跳过 {skipped}）"
            if eta is not None:
                message += f"；预计剩余约 {max(1, round(eta / 60))} 分钟"
            await report(message)

        judge = None
        async with AsyncExitStack() as stack:
            if self.model.configured:
                model = self.model
                if isinstance(model, JsonModel):
                    client = await stack.enter_async_context(
                        httpx.AsyncClient(
                            timeout=self.settings.model_timeout_seconds,
                            follow_redirects=False,
                            limits=httpx.Limits(
                                max_connections=config.concurrency,
                                max_keepalive_connections=config.concurrency,
                            ),
                        )
                    )
                    model = JsonModel(self.settings, client=client)
                judge = SourceModelJudge(
                    CompletionAdapter(model),
                    version=f"{self.settings.model_provider}/{self.settings.model_name or 'default'}:{JUDGE_REVISION}:{digest(str(self.settings.model_base_url))}",
                    namespace=corpus.namespace,
                    concise_quotes=config.retrieval_policy == "balanced",
                    cache_path=self.settings.data_dir
                    / "resolution"
                    / "cache"
                    / f"{digest(corpus.namespace)}.sqlite3",
                )
            try:
                await report("正在检索候选及同输入基线对比")
                await compare_methods(
                    corpus,
                    self.experiments_root / run.id,
                    config=config,
                    gold=options.gold,
                    judge=judge,
                    vectors=options.vectors,
                    method=options.method,
                    synonyms=options.synonyms,
                    max_alias_calls=options.max_alias_calls,
                    alias_progress=alias_progress,
                    pair_progress=pair_progress,
                    on_decision=on_decision,
                )
            finally:
                if judge:
                    judge.close()
        experiment = await asyncio.to_thread(load_experiment, self.experiments_root, run.id)
        resolution = SimpleNamespace(**experiment.results[options.method])
        alias_report = (
            json.loads(experiment.artifacts["alias_expansion.json"])
            if "alias_expansion.json" in experiment.artifacts
            else None
        )
        by_id = {node["id"]: node for node in graph["nodes"]}
        mentions = {mention.mention_id: mention for mention in corpus.mentions}
        if alias_report:
            expanded = {row["mention_id"]: row for row in alias_report["records"]}
            mentions = {
                mid: replace(
                    mention,
                    aliases=tuple(
                        dict.fromkeys([*mention.aliases, *expanded.get(mid, {}).get("aliases", [])])
                    ),
                )
                for mid, mention in mentions.items()
            }
        candidates = [
            candidate_from_decision(decision, by_id, mentions, run.source_kind)
            for decision in resolution.decisions
        ]
        # Only the engine's accepted complete-link partition may be applied.
        # Pairwise 'same' alone can otherwise bridge explicit negative evidence.
        if options.model_policy == "apply":
            partitions = {
                mid: entity["entity_id"]
                for entity in resolution.entities
                for mid in entity["mention_ids"]
            }
            unsupported_groups = {
                partitions[mid]
                for candidate in candidates
                if candidate.evidence.get("quote_validation", {}).get("supported") is False
                and candidate.evidence.get("accepted") is True
                for mid in candidate.node_ids
            }
            for candidate in candidates:
                accepted = candidate.evidence.get("accepted") is True
                groups = {partitions[mid] for mid in candidate.node_ids}
                if accepted and len(groups) == 1 and not groups & unsupported_groups:
                    candidate.status = "merged"
                    candidate.canonical_id = candidate.node_ids[0]
                    run.revision += 1
                    candidate.decision_order = run.revision
                    run.audits.append(
                        Audit(
                            id=str(uuid4()),
                            action="merge",
                            candidate_id=candidate.id,
                            canonical_id=candidate.canonical_id,
                            reviewer="消歧引擎 · 自动应用",
                            note="按所选 apply 策略应用引擎已接受的合并，可人工撤销",
                            created_at=now(),
                            revision=run.revision,
                            previous_status="pending",
                            source_nodes=candidate.nodes,
                        )
                    )
        candidates.sort(key=lambda row: (-row.score, row.id))
        diagnostics = {
            **resolution.diagnostics,
            "source_sha256": digest(graph),
            "root_source_id": graph.get("root_source_id", graph["source_id"]),
            "corpus_sha256": corpus.sha256,
            "method": resolution.method,
            "source_complete": True,
            "retrieval_complete": not bool(resolution.diagnostics.get("candidate_overflow")),
            "score_kind": "name_alias_similarity_not_identity_probability",
            "model_configured": self.model.configured,
            "review_policy": options.model_policy,
            "experiment_id": run.id,
            "alias_expansion": alias_report,
            "warnings": [
                "候选检索达到上限，可能存在未列出的匹配；原始节点均保留，可通过人工指定节点补充校验"
            ]
            if resolution.diagnostics.get("candidate_overflow")
            else [],
        }
        if config.retrieval_policy == "balanced":
            diagnostics["warnings"].append(
                "精筛候选使用真实名称、别名、编号、类型及稀有描述词；数据库事件按参与方、事件类型和发生时间召回，行号展示名称不参与匹配。未召回不代表确认不同，原始节点均保留，可人工补充"
            )
        missing_hints = sum(
            m.recall is not None and not m.recall.names and not m.recall.keys and not m.identifiers
            for m in corpus.mentions
        )
        diagnostics["records_without_recall_hints"] = missing_hints
        if missing_hints:
            diagnostics["warnings"].append(
                f"{missing_hints:,} 个节点缺少可用的名称、身份编号或完整事件线索；"
                "已保留原节点，未按行号或参与方编号猜测身份，可补充字段或人工指定匹配"
            )
        errors = {}
        for decision in resolution.decisions:
            if decision["reason"] == "JUDGE_FAILED":
                code = decision.get("error_code", decision.get("error_type", "UNKNOWN"))
                errors[code] = errors.get(code, 0) + 1
        diagnostics["judge_failure_reasons"] = errors
        if not self.model.configured:
            diagnostics["warnings"].append(
                "尚未配置聊天模型，未生成模型合并建议；请配置模型后重新分析"
            )
        if alias_report and any(row["state"] != "complete" for row in alias_report["records"]):
            diagnostics["warnings"].append(
                "部分节点的模型别名扩展未完成（预算、上下文或模型返回限制）；仍使用原名称和已有别名检索，原始节点均保留"
            )
        failures = resolution.diagnostics.get("judge_failures", 0)
        skipped = sum(d["reason"] == "MODEL_BUDGET_EXHAUSTED" for d in resolution.decisions)
        if failures or skipped:
            diagnostics["warnings"].append(
                f"模型判断失败 {failures} 对，预算跳过 {skipped} 对；未生成合并建议，详情保留在分析记录中"
            )
        return candidates, diagnostics

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
