"""Coordinate source interpretation, retrieve matching and reviewable graph templates."""

import asyncio
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from .catalog import Catalog
from .interpretation import FieldInterpreter, ModelPort
from .matching import decide, reviewable_candidate
from .models import (
    AlignmentError,
    ColumnMapping,
    ColumnProposal,
    MappingResult,
    MatchTrace,
    SourceTable,
    TableMapping,
    TableMeaning,
    TableProposal,
)
from .planning import retrieval_query
from .relations import map_relations
from .retrieval import RetrievalPort
from .templates import EntitySuggestion, TemplateProperty, draft_template
from .tracing import AnalysisProgress, Checkpoint


class Analyzer:
    def __init__(
        self,
        model: ModelPort,
        retriever: RetrievalPort,
        threshold: float = 0.75,
        relation_index=None,
        *,
        batch_columns: int = 32,
        model_concurrency: int = 3,
    ):
        self.model, self.retriever, self.threshold = model, retriever, threshold
        self.relation_index = relation_index
        self.batch_columns, self.model_concurrency = batch_columns, model_concurrency

    async def analyze(
        self,
        sources: list[SourceTable],
        catalog: Catalog,
        progress: Callable[[str], Awaitable[None]],
        checkpoint: Checkpoint | None = None,
    ) -> MappingResult:
        mappings = [self._base(source) for source in sources]
        result = MappingResult(
            revision=catalog.revision,
            snapshot_sha256=catalog.sha256,
            tables=mappings,
            relations=[],
            warnings=[
                "本体节点由 retrieve 匹配；得分不是正确概率，低分候选会标注为建议，可整体采纳或修改。",
                "按已确认的对象类型、身份字段和属性生成实例；同一身份的记录可合并。",
            ],
        )
        reporter = AnalysisProgress(result, progress, checkpoint)
        proposals, templates = {}, {}
        schemas = [
            {
                "table_id": s.table.id,
                "name": s.table.name,
                "columns": [c.name for c in s.table.columns],
                "keys": [c.name for c in s.table.columns if c.primary_key]
                + [c for fk in s.table.foreign_keys for c in fk.columns],
            }
            for s in sources
        ]
        async with self.retriever.session(catalog.revision) as session:
            for source, mapping in zip(sources, mappings, strict=True):
                if not source.rows:
                    mapping.reason = "空表没有可生成的实例，本次跳过"
                    await reporter.stop(mapping, mapping.reason)
                    continue
                try:
                    proposal = await self._table(
                        source, mapping, schemas, catalog, session, reporter
                    )
                    proposals[source.table.id] = proposal.relations
                    templates[source.table.id] = proposal
                except (AlignmentError, ValidationError) as exc:
                    mapping.status = "failed"
                    mapping.reason = (
                        exc.message
                        if isinstance(exc, AlignmentError)
                        else "字段含义分析格式不符合约定，本表未完成分析"
                    )
                    await reporter.stop(mapping, mapping.reason, failed=True)
        await reporter.emit("正在校验表间连接字段及关联唯一性")
        result.relations = await asyncio.to_thread(
            map_relations, sources, mappings, proposals, self.relation_index
        )
        if self.relation_index is not None:
            result.template = draft_template(
                sources, mappings, result.relations, templates, catalog
            )
            result.warnings.append(
                "已生成完整方案；无需逐项确认，可调整后整体采纳并生成。没有有效对象候选的表会说明原因。"
            )
            if result.template.note:
                result.warnings.append(result.template.note)
        return result

    async def _table(self, source, mapping, schemas, catalog, session, reporter):
        trace = mapping.trace
        rows = source.rows
        indices = sorted({i * (len(rows) - 1) // 4 for i in range(5)})
        trace.sample_rows = [rows[i].number for i in indices]
        await reporter.step(mapping, "meaning", "running", "正在解释表及字段含义，不选择本体节点")

        async def progress(detail):
            await reporter.step(mapping, "meaning", "running", detail)

        interpretation = await FieldInterpreter(
            self.model, self.batch_columns, self.model_concurrency
        ).interpret(source, schemas, indices, progress)
        plan = interpretation.plan
        mapping.structure_notes.extend(interpretation.notes)
        if len(plan.entities) > 1:
            mapping.structure_notes.append(
                "模型提示这张表包含多个对象或角色："
                + "、".join(e.query for e in plan.entities)
                + "。请核对字段分组、身份标识及关联依据后再确认生成规则。"
            )
        plan.query = retrieval_query(source.table.name, plan.query, table=True)
        source_columns = {c.name: c for c in source.table.columns}
        for column in plan.columns:
            column.query = retrieval_query(
                column.column, column.query, source_columns[column.column].comment
            )
        trace.meaning = TableMeaning(
            meaning=plan.meaning,
            search_terms=[plan.query[:80]],
            attribute_terms=list(dict.fromkeys(c.query[:80] for c in plan.columns))[:30],
        )
        await reporter.step(mapping, "meaning", "completed", "已整理表、字段和实体分组的检索词")
        await reporter.step(mapping, "recall", "running", "正在调用 retrieve 匹配表和每个字段")
        targets = [("table", source.table.name, plan.query)]
        targets += [("column", c.column, c.query) for c in plan.columns]
        targets += [("entity", e.id, e.query) for e in plan.entities]
        # The transport deduplicates identical queries within and across tables in this run.
        # Persist progress in bounded groups so wide-table retrieval remains observable.
        for offset in range(0, len(targets), 24):
            batch = targets[offset : offset + 24]
            responses = await session.search_many([q for _, _, q in batch])
            trace.retrievals.extend(
                decide(response, target, name, catalog, self.threshold)
                for (target, name, _), response in zip(batch, responses, strict=True)
            )
            await reporter.step(
                mapping,
                "recall",
                "running",
                f"retrieve 已处理 {len(trace.retrievals)} / {len(targets)} 项",
            )
        table_match, *other_matches = trace.retrievals
        trace.candidates = table_match.candidates
        trace.attribute_candidates = list(
            {c.id: c for m in other_matches if m.target == "column" for c in m.candidates}.values()
        )
        await reporter.step(
            mapping, "recall", "completed", f"已记录 {len(targets)} 项匹配及候选得分"
        )
        await reporter.step(mapping, "selection", "running", "按接口命中、分数和本体版本确定建议")
        trace.selected = table_match.selected
        trace.selection_reason = table_match.detail
        trace.selection_confidence = table_match.selected.score if table_match.selected else None
        mapping.reason = table_match.detail
        mapping.confidence = trace.selection_confidence or 0
        candidate = reviewable_candidate(table_match, catalog)
        mapping.concept_id = candidate.id if candidate else None
        mapping.concept_name = candidate.name if candidate else None
        mapping.status = {
            "matched": "mapped",
            "review": "review",
            "unmatched": "unmatched",
            "unavailable": "review",
            "mismatch": "review",
        }[table_match.status]
        mapping.verification = (
            table_match.status if table_match.status in {"mismatch", "unavailable"} else "verified"
        )
        trace.verification = mapping.verification
        proposal = self._proposal(plan, trace.retrievals, mapping)
        await reporter.step(mapping, "selection", "completed", table_match.detail)
        await reporter.step(mapping, "validation", "running", "正在校验字段完整性及来源键约束")
        mapping.columns = self._columns(source, proposal, catalog, mapping.warnings)
        pending = sum(m.status != "matched" for m in other_matches)
        if pending:
            mapping.warnings.append(
                f"{pending} 项字段或分组存在不确定性；未匹配字段保留原始属性，不阻止整表生成"
            )
        if mapping.status != "mapped" and mapping.concept_id:
            mapping.warnings.append("整表候选分数不足，仅作为建议；可整体采纳方案或修改对象类型")
        await reporter.step(
            mapping, "validation", "completed", "校验完成；字段与分组的待确认项已标出"
        )
        return proposal

    @staticmethod
    def _proposal(plan, matches, mapping):
        fields = {m.name: m for m in matches if m.target == "column"}
        groups = {m.name: m for m in matches if m.target == "entity"}
        entities = [
            EntitySuggestion(
                id=e.id,
                concept_id=groups[e.id].selected.id,
                key_columns=e.key_columns,
                properties=e.properties,
            )
            for e in plan.entities
            if groups[e.id].status in {"matched", "review"} and groups[e.id].selected
        ]
        covered = {p.column for e in entities for p in e.properties}
        missing = [c.column for c in plan.columns if c.column not in covered]
        primary = next((e for e in plan.entities if e.query == plan.query), None)
        if missing and primary and len(entities) == len(plan.entities):
            # Preserve unassigned source metadata on the explicitly identified primary object.
            owner = next(e for e in entities if e.id == primary.id)
            owner.properties.extend(TemplateProperty(column=c, name=c) for c in missing)
            mapping.structure_notes.append(
                f"{len(missing)} 个未分组字段作为原始属性保留在主对象“{primary.query}”，可在高级配置中调整。"
            )
        # Partial grouping must not drop columns or silently remove a transaction participant.
        if len(entities) != len(plan.entities) or (
            entities
            and not {c.column for c in plan.columns}
            <= {p.column for e in entities for p in e.properties}
        ):
            entities = []
            mapping.warnings.append("实体分组未全部匹配或未覆盖所有字段，保留整表草稿供人工调整")
            mapping.structure_notes.append(
                "分组匹配不完整，当前仅保留整表草稿，尚不能证明整行就是同一个业务对象。"
                "可在高级配置中补充分组；整体采纳时采用当前展示的对象与属性。"
            )
        return TableProposal(
            concept_id=mapping.concept_id,
            confidence=mapping.confidence,
            reason=mapping.reason,
            columns=[
                ColumnProposal(
                    column=c.column,
                    role=c.role,
                    semantic=c.semantic,
                    reason=c.reason,
                    concept_id=fields[c.column].selected.id
                    if fields[c.column].status == "matched"
                    else None,
                )
                for c in plan.columns
            ],
            relations=plan.relations,
            entities=entities,
            edges=plan.edges if entities else [],
        )

    def _base(self, source: SourceTable) -> TableMapping:
        return TableMapping(
            table_id=source.table.id,
            batch_id=source.batch.id,
            table_name=source.table.name,
            source_name=source.batch.name,
            row_count=source.table.row_count,
            trace=MatchTrace(method="retrieve", confidence_threshold=self.threshold),
            columns=[
                ColumnMapping(
                    column=c.name,
                    role="primary_key" if c.primary_key else "attribute",
                    property_key=f"field_{i:03d}",
                )
                for i, c in enumerate(source.table.columns)
            ],
        )

    @staticmethod
    def _columns(source, proposal, catalog, warnings):
        proposed = {c.column: c for c in proposal.columns}
        foreign = {c for fk in source.table.foreign_keys for c in fk.columns}
        columns = []
        for i, column in enumerate(source.table.columns):
            item = proposed[column.name].model_copy(deep=True)
            if column.primary_key:
                item.role = "primary_key"
            elif column.name in foreign:
                item.role = "foreign_key"
            elif item.role in {"primary_key", "foreign_key"}:
                item.role = "attribute"
                warnings.append(f"字段 {column.name} 未声明键约束，已保留为普通属性")
            columns.append(
                ColumnMapping(
                    **item.model_dump(),
                    property_key=f"field_{i:03d}",
                    concept_name=catalog.names.get(item.concept_id),
                )
            )
        return columns
