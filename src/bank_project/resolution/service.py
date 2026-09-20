"""Complete graph resolution using the migrated GraphRAG evidence engine."""

import asyncio
import inspect
import json
import logging
import sqlite3
from contextlib import suppress
from types import SimpleNamespace

from bank_project.alignment.model_client import JsonModel
from bank_project.alignment.models import AlignmentError

from .adapter import candidate_from_decision, corpus_from_graph, validate_graph
from .engine.candidates import retrieve
from .engine.contracts import ResolverConfig, digest
from .engine.model_judge import LLMJudge
from .engine.resolver import resolve_evidence
from .engine.synonyms import expand_corpus
from .projection import update_run
from .store import ResolutionStore

logger = logging.getLogger(__name__)


class CompletionAdapter:
    """Use the project's bounded model transport with the original evidence prompts."""

    def __init__(self, model):
        self.model = model

    async def completion_async(self, *, messages, **kwargs):
        value = await self.model.complete(messages[0]["content"], messages[1]["content"])
        return SimpleNamespace(content=json.dumps(value, ensure_ascii=False))


class ResolutionService:
    def __init__(self, settings, graph_loader):
        self.settings, self.graph_loader = settings, graph_loader
        self.store = ResolutionStore(settings.data_dir / "resolution" / "runs.sqlite3")
        self.tasks = set()
        self.model = JsonModel(settings)

    async def start(self, request):
        run, owner = await asyncio.to_thread(
            self.store.create, request.source_kind, request.source_id
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
        return self.store.decide(run_id, request)

    def manual(self, run_id, request):
        return self.store.decide(run_id, request, manual=True)

    def graph(self, run_id):
        return self.store.graph(run_id)

    async def _heartbeat(self, run_id, owner):
        while True:
            await asyncio.sleep(10)
            await asyncio.to_thread(self.store.heartbeat, run_id, owner)

    async def _execute(self, run, owner):
        heartbeat = asyncio.create_task(self._heartbeat(run.id, owner))
        try:
            raw = await asyncio.to_thread(self.graph_loader, run.source_kind, run.source_id)
            if inspect.isawaitable(raw):
                raw = await raw
            graph = await asyncio.to_thread(validate_graph, raw, run.source_kind, run.source_id)
            run.name = graph.get("name") or run.name
            await asyncio.to_thread(
                self.store.progress,
                run.id,
                owner,
                f"已读取完整图谱：{len(graph['nodes'])} 节点 / {len(graph['edges'])} 关系；正在检索与比较候选",
            )
            run.candidates, run.diagnostics = await self._analyze(run, graph)
            if heartbeat.done():
                heartbeat.result()
            run.status, run.error = "ready", None
            update_run(run, graph)
            run.progress = f"候选分析完成；{run.summary.pending_count} 组等待人工校验，原图保持完整"
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

    async def _analyze(self, run, graph):
        corpus = await asyncio.to_thread(corpus_from_graph, graph)
        config = ResolverConfig(model_policy="review", max_model_calls=200, concurrency=3)
        judge = None
        if self.model.configured:
            judge = LLMJudge(
                CompletionAdapter(self.model),
                version=f"{self.settings.model_provider}/{self.settings.model_name or 'default'}:bank-resolution-v1",
                namespace=corpus.namespace,
                cache_path=self.settings.data_dir / "resolution" / "cache" / f"{run.id}.sqlite3",
            )
        retrieval_corpus, alias_report = corpus, None
        try:
            if judge:
                retrieval_corpus, alias_report = await expand_corpus(
                    corpus, judge, config, max_alias_calls=200
                )
            candidate_data = await asyncio.to_thread(retrieve, retrieval_corpus, config)
            resolution = await resolve_evidence(
                corpus,
                config,
                judge,
                retrieval_corpus=retrieval_corpus,
                candidate_data=candidate_data,
            )
        finally:
            if judge:
                judge.close()
        by_id = {node["id"]: node for node in graph["nodes"]}
        mentions = {mention.mention_id: mention for mention in retrieval_corpus.mentions}
        candidates = [
            candidate_from_decision(decision, by_id, mentions) for decision in resolution.decisions
        ]
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
            "review_policy": "all_merges_require_explicit_review",
            "alias_expansion": alias_report,
            "warnings": [
                "候选检索达到上限，可能存在未列出的匹配；原始节点均保留，可通过人工指定节点补充校验"
            ]
            if resolution.diagnostics.get("candidate_overflow")
            else [],
        }
        if alias_report and any(row["state"] != "complete" for row in alias_report["records"]):
            diagnostics["warnings"].append(
                "部分节点的模型别名扩展未完成（预算、上下文或模型返回限制）；仍使用原名称和已有别名检索，原始节点均保留"
            )
        return candidates, diagnostics

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
