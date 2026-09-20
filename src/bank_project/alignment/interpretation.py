"""Bounded field interpretation with one shared outline and deterministic, complete merging."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from .models import AlignmentError, IncompleteModelOutput, SourceTable
from .planning import (
    COLUMNS_SYSTEM,
    OUTLINE_SYSTEM,
    SYSTEM,
    ColumnBatch,
    ColumnIntent,
    EntityIntent,
    TableIntent,
    TableOutline,
)
from .templates import TemplateProperty

MAX_PROMPT_CHARS = 48_000
MAX_CONTEXT_CHARS = 8_000


class ModelPort(Protocol):
    async def complete(self, system: str, user: str) -> dict: ...


class IncompleteFieldBatch(AlignmentError):
    """A model response did not preserve the exact set of requested source fields."""


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def validate_columns(expected: list[str], actual: list[str]):
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise IncompleteFieldBatch("字段含义分析有遗漏、重复或不属于当前批次，本表未采用该结果")


def related_schemas(schemas: list[dict], names: set[str]) -> list[dict]:
    """Pass bounded join clues, not every selected table's entire schema on every call."""
    result = []
    for schema in schemas:
        candidates = [c for c in schema["columns"] if c in names or c in schema["keys"]]
        item = {
            "table_id": schema["table_id"],
            "name": schema["name"],
            "column_count": len(schema["columns"]),
            "columns": candidates[:16],
            "columns_complete": len(candidates[:16]) == len(schema["columns"]),
        }
        if len(encode(result + [item])) > MAX_CONTEXT_CHARS:
            item.update(columns=[], columns_complete=False)
        result.append(item)
    return result


@dataclass
class Interpretation:
    plan: TableIntent
    notes: list[str]


class FieldInterpreter:
    def __init__(self, model: ModelPort, batch_columns: int = 32, concurrency: int = 3):
        if not 1 <= batch_columns <= 64 or not 1 <= concurrency <= 8:
            raise ValueError("Invalid interpretation batch limits")
        self.model, self.batch_columns, self.concurrency = model, batch_columns, concurrency

    async def interpret(
        self,
        source: SourceTable,
        schemas: list[dict],
        samples: list[int],
        progress: Callable[[str], Awaitable[None]],
    ) -> Interpretation:
        names = [c.name for c in source.table.columns]
        if len(names) > 2048:
            raise AlignmentError("单表字段超过 2,048 列，请按业务拆表后分析")
        context = related_schemas(schemas, set(names))
        # Small tables retain the original single-call path, within the same input budget.
        if len(names) <= self.batch_columns:
            prompt = encode(
                {
                    "table": source.table.model_dump(),
                    "samples": [
                        [None if v is None else v[:80] for v in source.rows[i].values]
                        for i in samples
                    ],
                    "selected_tables": context,
                }
            )
            if len(prompt) <= MAX_PROMPT_CHARS:
                try:
                    plan = TableIntent.model_validate(await self.model.complete(SYSTEM, prompt))
                    validate_columns(names, [c.column for c in plan.columns])
                    return Interpretation(plan, [])
                except IncompleteModelOutput:
                    await progress("整表输出达到上限，改为概览与字段分批解释")

        await progress(f"共 {len(names):,} 列，正在整理整表含义与统一分组")
        outline, notes = await self._outline(source, context, samples)
        groups = {e.id for e in outline.entities}
        if len(groups) != len(outline.entities) or any(
            not e.query.strip() for e in outline.entities
        ):
            raise AlignmentError("整表概览中的分组重复或检索词为空")
        if not outline.query.strip() or any(
            not set(e.key_columns) <= set(names) for e in outline.entities
        ):
            raise AlignmentError("整表概览的检索词为空或身份字段不存在")
        if any(e.source not in groups or e.target not in groups for e in outline.edges):
            raise AlignmentError("整表概览的关系引用了不存在的分组")

        def prompt(indices):
            return encode(
                {
                    "table": {"id": source.table.id, "name": source.table.name},
                    "overview": outline.model_dump(exclude={"relations"}),
                    "column_count": len(names),
                    "columns": [source.table.columns[i].model_dump() for i in indices],
                    "samples": [
                        [
                            None if (v := source.rows[r].values[i]) is None else v[:80]
                            for i in indices
                        ]
                        for r in samples
                    ],
                }
            )

        batches = self._batches(len(names), prompt)
        columns = await self._interpret_batches(batches, names, groups, prompt, progress)
        # Result order is always source order, independent of model order or concurrent completion.
        validate_columns(names, [c.column for c in columns])
        fields = {c.column: c for c in columns}
        columns = [fields[n] for n in names]
        if groups and any(c.entity_id is None for c in columns):
            notes.append("部分字段无法确定所属对象；已保留全部字段，请人工核对分组后确认生成规则。")
        return Interpretation(
            TableIntent(
                **outline.model_dump(exclude={"entities"}),
                columns=[ColumnIntent(**c.model_dump(exclude={"entity_id"})) for c in columns],
                entities=[
                    EntityIntent(
                        **e.model_dump(),
                        properties=[
                            TemplateProperty(column=c.column, name=c.column)
                            for c in columns
                            if c.entity_id == e.id
                        ],
                    )
                    for e in outline.entities
                ],
            ),
            notes,
        )

    async def _outline(self, source, context, samples):
        catalog = [[c.name, c.comment[:120]] if c.comment else c.name for c in source.table.columns]
        # Keep common leading identifiers plus distributed fields, without sending a wide row.
        sample_columns = (
            sorted(
                set(range(min(8, len(catalog)))) | {i * (len(catalog) - 1) // 7 for i in range(8)}
            )
            if catalog
            else []
        )
        data = {
            "table": {"id": source.table.id, "name": source.table.name},
            "column_count": len(catalog),
            "catalog": catalog,
            "catalog_complete": True,
            "primary_keys": [c.name for c in source.table.columns if c.primary_key],
            "sample_columns": [source.table.columns[i].name for i in sample_columns],
            "samples": [
                [
                    None if (v := source.rows[r].values[i]) is None else v[:80]
                    for i in sample_columns
                ]
                for r in samples
            ],
            "selected_tables": context,
        }
        while len(encode(data)) > MAX_PROMPT_CHARS and len(data["catalog"]) > 1:
            count = max(1, len(data["catalog"]) // 2)
            data["catalog"] = [
                catalog[i * (len(catalog) - 1) // max(1, count - 1)] for i in range(count)
            ]
            data["catalog_complete"] = False
        if len(encode(data)) > MAX_PROMPT_CHARS:
            raise AlignmentError("表名或键结构超过分析上限，请精简元数据后重试")
        notes = (
            []
            if data["catalog_complete"]
            else [
                "字段目录过长，整表概览仅使用分布抽样的目录；每列仍逐批解释，请人工核对对象分组。"
            ]
        )
        try:
            value = await self.model.complete(OUTLINE_SYSTEM, encode(data))
            return TableOutline.model_validate(value), notes
        except AlignmentError as exc:
            raise AlignmentError(f"整表概览未完成：{exc.message}", exc.status) from exc

    def _batches(self, count, prompt):
        batches, batch = [], []
        for index in range(count):
            candidate = [*batch, index]
            if len(candidate) > self.batch_columns or len(prompt(candidate)) > MAX_PROMPT_CHARS:
                if not batch:
                    raise AlignmentError(f"第 {index + 1} 列元数据过大，请精简字段说明后重试")
                batches.append(batch)
                batch = [index]
                if len(prompt(batch)) > MAX_PROMPT_CHARS:
                    raise AlignmentError(f"第 {index + 1} 列元数据过大，请精简字段说明后重试")
            else:
                batch = candidate
        if batch:
            batches.append(batch)
        return batches

    async def _interpret_batches(self, batches, names, groups, prompt, progress):
        async def interpret(indices):
            try:
                response = await self.model.complete(COLUMNS_SYSTEM, prompt(indices))
                columns = ColumnBatch.model_validate(response).columns
                validate_columns([names[i] for i in indices], [c.column for c in columns])
            except (IncompleteModelOutput, IncompleteFieldBatch):
                if len(indices) == 1:
                    raise
                middle = len(indices) // 2
                return await interpret(indices[:middle]) + await interpret(indices[middle:])
            if any(
                not c.query.strip() or (c.entity_id is not None and c.entity_id not in groups)
                for c in columns
            ):
                raise AlignmentError("检索词为空或引用了概览以外的分组")
            return columns

        async def run(index, indices):
            try:
                return await interpret(indices)
            except (AlignmentError, ValidationError) as exc:
                detail = (
                    exc.message if isinstance(exc, AlignmentError) else "返回格式不符合字段约定"
                )
                raise AlignmentError(
                    f"第 {index + 1}/{len(batches)} 批（第 {indices[0] + 1}–{indices[-1] + 1} 列）失败：{detail}"
                ) from exc

        pending, remaining, columns, completed = {}, iter(enumerate(batches)), [], 0

        async def report():
            await progress(
                f"字段解释已完成 {completed}/{len(batches)} 批（{len(columns):,}/{len(names):,} 列），"
                f"最多 {self.concurrency} 批并发；随后执行 retrieve 匹配"
            )

        def fill():
            while len(pending) < self.concurrency:
                item = next(remaining, None)
                if item is None:
                    break
                index, indices = item
                pending[asyncio.create_task(run(index, indices))] = index

        try:
            await report()
            fill()
            while pending:
                done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in sorted(done, key=lambda t: pending[t]):
                    columns.extend(task.result())
                    del pending[task]
                    completed += 1
                    await report()
                fill()
            return columns
        finally:
            # A failed batch must cancel siblings before the next table can start.
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
