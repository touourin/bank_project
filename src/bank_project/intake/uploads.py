"""Stream HTTP uploads to disk before enqueueing; never retain the whole body in RAM."""

import os
import shutil
from hashlib import sha256
from uuid import uuid4

import anyio
from starlette.concurrency import run_in_threadpool

from .models import IntakeError

RESERVE_BYTES = 1024 * 1024 * 1024


def check_disk(directory):
    if shutil.disk_usage(directory).free < RESERVE_BYTES:
        raise IntakeError("上传磁盘剩余空间不足 1 GiB，请清理空间后重试", status=507)


async def receive_upload(request, filename, options, limit):
    state = request.app.state
    try:
        state.upload_slots.acquire_nowait()
    except anyio.WouldBlock as exc:
        raise IntakeError("已有两个文件正在上传，请稍后重试", status=429) from exc
    path, enqueue_attempted = None, False
    try:
        directory = state.settings.data_dir / "uploads"
        directory.mkdir(parents=True, exist_ok=True)
        check_disk(directory)
        path = directory / str(uuid4())
        digest, size, checked_at = sha256(), 0, 0
        async with await anyio.open_file(path, "xb") as file:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise IntakeError("文件超过上传大小上限", status=413)
                if size - checked_at >= 64 * 1024 * 1024:
                    check_disk(directory)
                    checked_at = size
                digest.update(chunk)
                await file.write(chunk)
            await file.flush()
            await run_in_threadpool(os.fsync, file.wrapped.fileno())
        if not size:
            raise IntakeError("文件为空")
        enqueue_attempted = True
        return await run_in_threadpool(
            state.intake_jobs.create, filename, path, options, digest.hexdigest(), size
        )
    except IntakeError as exc:
        if exc.status == 429:
            enqueue_attempted = False
        raise
    finally:
        # An uncertain DB commit may have queued the path. Retain it for recovery in that case.
        if path and not enqueue_attempted:
            path.unlink(missing_ok=True)
        state.upload_slots.release()
