"""Wide schemas must be bounded, complete and consistent across concurrent model calls."""

import asyncio
import json

import pytest
from test_alignment import FakeRetriever, catalog_bytes, source

from bank_project.alignment.analyzer import Analyzer
from bank_project.alignment.catalog import Catalog
from bank_project.alignment.interpretation import (
    MAX_PROMPT_CHARS,
    FieldInterpreter,
    related_schemas,
)
from bank_project.alignment.models import AlignmentError, IncompleteModelOutput
from bank_project.alignment.planning import COLUMNS_SYSTEM, OUTLINE_SYSTEM, SYSTEM


def wide_source(count=1334):
    names = [f"field_{i:04d}" for i in range(count)]
    return source(names=names, values=[[f"{r}-{i}" for i in range(count)] for r in range(7)])


class BatchModel:
    def __init__(self):
        self.calls = []
        self.active = self.peak = 0

    async def complete(self, system, user):
        data = json.loads(user)
        self.calls.append((system, data))
        assert len(user) <= MAX_PROMPT_CHARS
        if system == OUTLINE_SYSTEM:
            return {
                "meaning": "一行包含客户及账户信息",
                "query": "客户",
                "entities": [
                    {"id": "customer", "query": "客户", "key_columns": []},
                    {"id": "account", "query": "账户", "key_columns": []},
                ],
            }
        assert system == COLUMNS_SYSTEM
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            # Alternate completion order to exercise deterministic merging.
            index = int(data["columns"][0]["name"].split("_")[1])
            await asyncio.sleep(0.001 if index % 2 else 0.003)
            return self.fields(data)
        finally:
            self.active -= 1

    def fields(self, data):
        return {
            "columns": [
                {
                    "column": c["name"],
                    "query": "客户编号",
                    "entity_id": "customer" if int(c["name"].split("_")[1]) % 2 else "account",
                }
                for c in reversed(data["columns"])
            ]
        }


def interpret(model, s=None, batch_columns=32, concurrency=3):
    messages = []

    async def progress(message):
        messages.append(message)

    s = s or wide_source()
    result = asyncio.run(
        FieldInterpreter(model, batch_columns, concurrency).interpret(s, [], [0, 3, 6], progress)
    )
    return result, messages


def test_1334_columns_have_bounded_requests_shared_groups_and_complete_ordered_results():
    model = BatchModel()
    result, messages = interpret(model)
    assert model.peak == 3 and model.active == 0
    assert len(model.calls) == 1 + 42
    assert len(model.calls[0][1]["sample_columns"]) <= 16
    assert len(model.calls[0][1]["samples"]) == 3
    assert len(result.plan.columns) == 1334
    assert [c.column for c in result.plan.columns] == [c.name for c in wide_source().table.columns]
    assert len(result.plan.entities[0].properties) == 667
    assert len(result.plan.entities[1].properties) == 667
    assert all(int(p.column.split("_")[1]) % 2 for p in result.plan.entities[0].properties)
    assert "42/42 批（1,334/1,334 列）" in messages[-1]
    outlines = []
    for _system, data in model.calls[1:]:
        assert 1 <= len(data["columns"]) <= 32
        assert len(data["samples"]) == 3
        assert all(len(row) == len(data["columns"]) for row in data["samples"])
        outlines.append(data["overview"])
    assert all(o == outlines[0] for o in outlines)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "invented", "group", "blank"])
def test_bad_batch_cancels_siblings_and_never_calls_retrieve(fault):
    class BadModel(BatchModel):
        def fields(self, data):
            value = super().fields(data)
            columns = value["columns"]
            if fault == "missing":
                columns.pop()
            elif fault == "duplicate":
                columns.append(columns[0])
            elif fault == "invented":
                columns[0]["column"] = "other"
            elif fault == "group":
                columns[0]["entity_id"] = "invented"
            else:
                columns[0]["query"] = " "
            return value

    model, retriever, messages = BadModel(), FakeRetriever(), []

    async def progress(message):
        messages.append(message)

    result = asyncio.run(
        Analyzer(model, retriever).analyze([wide_source()], Catalog(catalog_bytes()), progress)
    )
    table = result.tables[0]
    assert table.status == "failed" and "批（第" in table.reason
    assert len(table.columns) == 1334  # Original fields survive the failure for inspection.
    assert not retriever.queries
    assert model.active == 0 and model.peak <= 3
    # Retries may subdivide the initial three batches, but never start unrelated batches.
    assert all(int(d["columns"][0]["name"].split("_")[1]) < 96 for _, d in model.calls[1:])
    assert table.trace.steps[0].status == "failed"
    assert "1,334" in messages[1]


def test_truncated_output_splits_only_affected_batches_within_concurrency_limit():
    class LimitedModel(BatchModel):
        def fields(self, data):
            if len(data["columns"]) > 3:
                raise IncompleteModelOutput("output limited")
            return super().fields(data)

    model = LimitedModel()
    result, messages = interpret(model, wide_source(17), batch_columns=8, concurrency=2)
    assert len(result.plan.columns) == 17
    assert model.peak <= 2 and model.active == 0
    assert "3/3 批（17/17 列）" in messages[-1]


def test_missing_fields_retry_only_the_affected_batch_and_keep_successful_work():
    class OmittingModel(BatchModel):
        def fields(self, data):
            value = super().fields(data)
            if data["columns"][0]["name"] == "field_0032" and len(data["columns"]) > 16:
                value["columns"].pop()
            return value

    model = OmittingModel()
    result, _ = interpret(model, wide_source(96))
    assert len(result.plan.columns) == 96
    assert len(model.calls) == 6  # Outline, three batches, two halves of the affected batch.
    starts = [d["columns"][0]["name"] for _, d in model.calls[1:]]
    assert starts.count("field_0000") == starts.count("field_0064") == 1


def test_input_size_also_splits_batches_and_oversized_single_field_is_reported():
    s = wide_source(5)
    for c in s.table.columns:
        c.comment = "说明" * 7000
    model = BatchModel()
    result, _ = interpret(model, s, batch_columns=4)
    assert len(result.plan.columns) == 5
    assert all(len(d["columns"]) <= 3 for _, d in model.calls[1:])
    s.table.columns[0].comment = "说明" * MAX_PROMPT_CHARS
    with pytest.raises(AlignmentError, match="第 1 列元数据过大"):
        interpret(BatchModel(), s, batch_columns=4)


def test_oversized_outline_is_explicitly_sampled_but_all_fields_are_still_interpreted():
    s = wide_source(600)
    for c in s.table.columns:
        c.comment = "字段的具体业务说明" * 20
    model = BatchModel()
    result, _ = interpret(model, s)
    assert model.calls[0][1]["catalog_complete"] is False
    assert len(result.plan.columns) == 600
    assert any("整表概览仅使用" in n for n in result.notes)


def test_unassigned_field_survives_and_requires_group_review():
    class UncertainModel(BatchModel):
        def fields(self, data):
            value = super().fields(data)
            value["columns"][0]["entity_id"] = None
            return value

    result, _ = interpret(UncertainModel(), wide_source(33))
    assert len(result.plan.columns) == 33
    assert sum(len(e.properties) for e in result.plan.entities) == 31
    assert "无法确定所属对象" in result.notes[0]


def test_cancellation_waits_for_inflight_model_calls():
    async def exercise():
        started = asyncio.Event()

        class WaitingModel(BatchModel):
            async def complete(self, system, user):
                if system == OUTLINE_SYSTEM:
                    return await super().complete(system, user)
                self.active += 1
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    self.active -= 1

        async def progress(_):
            pass

        model = WaitingModel()
        task = asyncio.create_task(
            FieldInterpreter(model).interpret(wide_source(), [], [0], progress)
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert model.active == 0

    asyncio.run(exercise())


def test_small_table_can_fall_back_to_batched_interpretation_on_output_limit():
    class LimitedModel(BatchModel):
        async def complete(self, system, user):
            if system == SYSTEM:
                self.calls.append((system, json.loads(user)))
                raise IncompleteModelOutput("output limited")
            return await super().complete(system, user)

    model = LimitedModel()
    result, messages = interpret(model, wide_source(5))
    assert [system for system, _ in model.calls] == [SYSTEM, OUTLINE_SYSTEM, COLUMNS_SYSTEM]
    assert len(result.plan.columns) == 5
    assert "改为概览与字段分批解释" in messages[0]


def test_other_wide_schemas_send_only_bounded_join_clues():
    schemas = [
        {
            "table_id": "other",
            "name": "related",
            "keys": ["customer_id"],
            "columns": ["customer_id", "common", *[f"long_other_field_{i}" for i in range(2046)]],
        }
    ]
    result = related_schemas(schemas, {"common", "local_only"})
    assert result[0]["columns"] == ["customer_id", "common"]
    assert result[0]["column_count"] == 2048 and result[0]["columns_complete"] is False
    assert len(json.dumps(result)) < 250
