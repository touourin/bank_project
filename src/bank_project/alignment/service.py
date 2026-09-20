"""Coordinate durable jobs without coupling source parsing, model transport or graph writes."""

import asyncio
import logging
from contextlib import suppress

from bank_project.settings import Settings

from .analyzer import Analyzer
from .catalog import Catalog
from .editing import revise_mapping
from .models import AlignmentError
from .retrieval import RetrievalFailure
from .sources import StagedSources
from .store import RunStore

logger = logging.getLogger(__name__)


class AlignmentService:
    def __init__(
        self, settings: Settings, sources: StagedSources, store: RunStore, analyzer: Analyzer, graph
    ):
        self.settings, self.sources, self.store = settings, sources, store
        self.analyzer, self.graph = analyzer, graph
        self.tasks: set[asyncio.Task] = set()

    def config(self):
        error, revision, count = None, None, 0
        try:
            catalog = Catalog.load(self.settings.ontology_snapshot, self.settings.ontology_revision)
            revision, count = catalog.revision, len(catalog.names)
        except AlignmentError as exc:
            error = exc.message
        return {
            "model_configured": bool(self.settings.model_api_key),
            "graph_configured": self.graph.configured,
            "catalog_error": error,
            "revision": revision,
            "concept_count": count,
            "matching_mode": "retrieve",
            "retrieve_configured": bool(self.settings.retrieve_base_url),
            "verification_mode": "external_retrieve",
            "confidence_threshold": self.settings.alignment_min_confidence,
        }

    async def analyze(self, selections):
        if not self.settings.model_api_key:
            raise AlignmentError("请先配置大模型密钥，再开始分析", 503)
        catalog = await asyncio.to_thread(
            Catalog.load, self.settings.ontology_snapshot, self.settings.ontology_revision
        )
        try:
            await self.analyzer.retriever.check_revision(catalog.revision)
        except RetrievalFailure as exc:
            raise AlignmentError(exc.detail, 503) from exc
        sources = []

        def read_sources():
            sources.extend(self.sources.read(selections))
            return sources

        run, sequence, owner = await asyncio.to_thread(self.store.create_from, read_sources)
        self._spawn(run, sequence, owner, "analyze", sources, catalog)
        return run

    async def generate(self, run_id, template=None):
        if not self.graph.configured:
            raise AlignmentError("请先配置业务 Neo4j，再生成图谱", 503)
        if template is not None:
            result = await asyncio.to_thread(self._approved_result, run_id, template)
        else:
            result = None
            current = await asyncio.to_thread(self.store.get, run_id)
            if current.result and current.result.template and not current.result.template.confirmed:
                raise AlignmentError("请先整体采纳生成方案", 409)
        run, sequence, owner = await asyncio.to_thread(self.store.start_graph, run_id, result)
        self._spawn(run, sequence, owner, "graph")
        return run

    def _editable_catalog(self, run_id):
        run = self.store.get(run_id)
        if run.status != "ready" or run.result is None or run.graph_status == "building":
            raise AlignmentError("请等待当前任务完成后再修改匹配", 409)
        catalog = Catalog.load(self.settings.ontology_snapshot, run.result.revision)
        if catalog.sha256 != run.result.snapshot_sha256:
            raise AlignmentError("本体快照已变化，请重新分析后再修改，避免混用版本", 409)
        return run, catalog

    def concepts(self, run_id, query):
        _, catalog = self._editable_catalog(run_id)
        return catalog.search(query)

    def edit(self, run_id, request):
        run, catalog = self._editable_catalog(run_id)
        result = revise_mapping(
            run.result, request, catalog, self.store.sources(run_id), self.analyzer.relation_index
        )
        return self.store.revise(run_id, result)

    def save_template(self, run_id, template):
        return self.store.revise(run_id, self._approved_result(run_id, template))

    def default_template(self, run_id, current):
        from .presets import row_record_template

        run, catalog = self._editable_catalog(run_id)
        return row_record_template(self.store.sources(run_id), run.result.tables, current, catalog)

    def _approved_result(self, run_id, template):
        from .templates import validate_template

        run, catalog = self._editable_catalog(run_id)
        sources = self.store.sources(run_id)
        result = run.result.model_copy(deep=True)
        result.template = validate_template(template, sources, catalog)
        return result

    def _spawn(self, *args):
        task = asyncio.create_task(self._execute(*args))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _execute(self, run, sequence, owner, kind, sources=None, catalog=None):
        async def progress(message):
            await asyncio.to_thread(self.store.update, run.id, sequence, owner, progress=message)

        async def checkpoint(message, result):
            await asyncio.to_thread(
                self.store.update,
                run.id,
                sequence,
                owner,
                progress=message,
                result=result.model_dump(),
            )

        async def heartbeat():
            while True:
                await asyncio.sleep(10)
                await asyncio.to_thread(self.store.heartbeat, sequence, owner)

        async def operation():
            if kind == "analyze":
                result = await self.analyzer.analyze(sources, catalog, progress, checkpoint)
                failed = sum(t.status == "failed" for t in result.tables)
                summary = (
                    f"分析结束，{failed}/{len(result.tables)} 张表失败，请检查原因后重试"
                    if failed
                    else "分析完成"
                )
                return {"status": "ready", "progress": summary, "result": result.model_dump()}
            stored = await asyncio.to_thread(self.store.sources, run.id)
            summary = await asyncio.to_thread(
                self.graph.publish,
                run,
                stored,
                sequence,
                lambda: self.store.heartbeat(sequence, owner),
            )
            return {
                "graph_status": "ready",
                "graph_version": summary.version,
                "progress": "该版本已生成图谱",
            }

        lease, work = asyncio.create_task(heartbeat()), asyncio.create_task(operation())
        try:
            done, _ = await asyncio.wait({lease, work}, return_when=asyncio.FIRST_COMPLETED)
            if lease in done:
                await lease
            changes = await work
            await asyncio.to_thread(
                self.store.update, run.id, sequence, owner, finish=True, **changes
            )
        except (Exception, asyncio.CancelledError) as exc:
            message = (
                exc.message
                if isinstance(exc, AlignmentError)
                else "任务未完成，请重试；详情请联系服务管理员"
            )
            if isinstance(exc, asyncio.CancelledError):
                message = "服务已停止，任务中断，请重新发起"
            # Only the exception class goes to logs; provider errors may contain sensitive payloads.
            logger.warning("Alignment job %s stopped (%s)", run.id, type(exc).__name__)
            changes = (
                {"status": "failed", "error": message, "progress": "分析未完成"}
                if kind == "analyze"
                else {"graph_status": "failed", "graph_error": message}
            )
            with suppress(Exception):
                await asyncio.to_thread(
                    self.store.update, run.id, sequence, owner, finish=True, **changes
                )
        finally:
            for task in (lease, work):
                task.cancel()
            await asyncio.gather(lease, work, return_exceptions=True)

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
