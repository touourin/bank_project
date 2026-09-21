"""Bounded workers keep making progress when individual requests are slow."""

import asyncio


async def bounded_map(items, callback, concurrency):
    """Consume a deterministic iterable without batch barriers or unbounded tasks."""
    pending = iter(items)

    async def worker():
        for item in pending:
            await callback(item)

    async with asyncio.TaskGroup() as group:
        for _ in range(concurrency):
            group.create_task(worker())
