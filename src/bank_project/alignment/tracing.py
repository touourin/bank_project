"""Persist observable analysis stages, never provider prompts or hidden reasoning."""

from collections.abc import Awaitable, Callable

from .models import MappingResult, TableMapping

Checkpoint = Callable[[str, MappingResult], Awaitable[None]]


class AnalysisProgress:
    def __init__(self, result, progress, checkpoint: Checkpoint | None):
        self.result, self.progress, self.checkpoint = result, progress, checkpoint

    async def emit(self, message: str):
        if self.checkpoint:
            await self.checkpoint(message, self.result)
        else:
            await self.progress(message)

    async def step(self, table: TableMapping, key: str, status: str, detail: str):
        assert table.trace is not None
        step = next(s for s in table.trace.steps if s.key == key)
        step.status, step.detail = status, detail
        await self.emit(f"{table.table_name}：{detail}")

    async def stop(self, table: TableMapping, message: str, *, failed: bool = False):
        assert table.trace is not None
        for step in table.trace.steps:
            if step.status == "running":
                step.status, step.detail = ("failed" if failed else "skipped"), message
            elif step.status == "pending":
                step.status, step.detail = "skipped", "前序步骤未完成，本步骤未执行"
        await self.emit(f"{table.table_name}：{message}")
