# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License; adapted from GraphRAG Search (see LICENSE).

"""Filesystem-backed document imports and indexing jobs (GraphRAG 2.5)."""

from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .parsers import Document

MANAGED_BY = "bank-graphrag-document-import-v1"
ACTIVE = {"queued", "running"}
_CHILDREN: dict[str, subprocess.Popen] = {}
_SECRET_FIELD = re.compile(r"key|secret|password|token|credential|connection_string", re.I)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _root(data_root: Path) -> Path:
    return Path(data_root).expanduser().resolve()


def _inside(root: Path, relative: str | Path) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise ValueError("数据路径必须位于数据根目录内。")
    return candidate


def _dataset(root: Path, key: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,100}", key):
        raise ValueError("无效的数据集标识。")
    return _inside(root, key)


@contextmanager
def _lock(path: Path, *, blocking: bool = True) -> Iterator[Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_NOFOLLOW also protects lock files against symlink redirection.
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield handle
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _listing(root: Path) -> list[dict]:
    path = _inside(root, "listing.json")
    if not path.exists():
        return []
    entries = _read_json(path)
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise ValueError("listing.json 必须是数据集列表。")
    return entries


def create_dataset(
    data_root: Path,
    *,
    name: str,
    description: str,
    documents: list[Document],
    chunk_size: int = 1200,
    overlap: int = 100,
    profile: str = "general",
) -> str:
    """Create a new isolated dataset, never altering an existing indexed dataset."""
    name = name.strip()
    from .profiles import profile_info

    profile_info(profile)
    if not name or len(name) > 200:
        raise ValueError("数据集名称需为 1–200 个字符。")
    if not isinstance(chunk_size, int) or not 100 <= chunk_size <= 16000:
        raise ValueError("分块大小需为 100–16000 tokens。")
    if not isinstance(overlap, int) or not 0 <= overlap < chunk_size:
        raise ValueError("重叠需大于等于 0 且小于分块大小。")
    if not documents:
        raise ValueError("请至少提供一篇有效文档。")
    root = _root(data_root)
    root.mkdir(parents=True, exist_ok=True)
    rows, duplicates, seen = [], [], {}
    for document in documents:
        text = document.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text or "\x00" in text:
            raise ValueError("文档正文为空或包含无效字符。")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in seen:
            duplicates.append(
                {
                    "source_name": document.source_name,
                    "title": document.title,
                    "duplicate_of": seen[digest],
                }
            )
            continue
        identifier = uuid.uuid4().hex
        seen[digest] = identifier
        rows.append(
            {
                "id": identifier,
                "title": document.title.strip() or document.source_name,
                "text": text,
                "source_name": document.source_name,
                "source_sha256": digest,
            }
        )
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "dataset"
    with _lock(_inside(root, ".ingestion.lock")):
        keys = {entry.get("key") for entry in _listing(root)}
        while True:
            key = f"{slug}-{uuid.uuid4().hex[:12]}"
            folder = _dataset(root, key)
            if key not in keys and not folder.exists():
                folder.mkdir(mode=0o700)
                break
        try:
            (folder / "input").mkdir()
            with (folder / "input" / "documents.csv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["id", "title", "text", "source_name", "source_sha256"],
                )
                writer.writeheader()
                writer.writerows(rows)
            created = _now()
            manifest = {
                "managed_by": MANAGED_BY,
                "key": key,
                "name": name,
                "description": description.strip(),
                "created_at": created,
                "document_count": len(rows),
                "duplicate_count": len(duplicates),
                "duplicates": duplicates,
                "documents": [
                    {field: value for field, value in row.items() if field != "text"}
                    for row in rows
                ],
                "chunk_size": chunk_size,
                "overlap": overlap,
                "profile": profile,
            }
            _atomic_json(folder / "manifest.json", manifest)
            _atomic_json(
                folder / "job.json",
                {
                    **{
                        field: manifest[field]
                        for field in (
                            "key",
                            "name",
                            "description",
                            "created_at",
                            "document_count",
                            "duplicate_count",
                            "profile",
                        )
                    },
                    "status": "ready",
                    "stage": "等待开始",
                    "progress": 0.0,
                    "error": None,
                    "updated_at": created,
                    "pid": None,
                    "attempt": 0,
                    "run_id": None,
                },
            )
        except Exception:
            shutil.rmtree(folder)
            raise
    return key


def _managed_folder(root: Path, key: str) -> Path:
    folder = _dataset(root, key)
    manifest = _read_json(_inside(root, folder / "manifest.json"))
    if manifest.get("managed_by") != MANAGED_BY or manifest.get("key") != key:
        raise ValueError("只能管理通过文档导入创建的新数据集。")
    return folder


def _alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _run_busy(folder: Path) -> bool:
    try:
        with _lock(_inside(folder, ".run.lock"), blocking=False):
            return False
    except BlockingIOError:
        return True


def _refresh_job(folder: Path, job: dict) -> dict:
    if job["status"] not in ACTIVE:
        return job
    child = _CHILDREN.get(str(folder))
    if child is not None and child.poll() is not None:
        _CHILDREN.pop(str(folder), None)
    busy = _run_busy(folder)
    starting = time.time() - job.get("launch_time", 0) < 120 and _alive(job.get("pid"))
    if not busy and not starting:
        job.update(
            status="failed",
            stage="索引进程已停止",
            error="索引进程意外结束，可以重试。",
            updated_at=_now(),
            pid=None,
        )
        _atomic_json(folder / "job.json", job)
    return job


def get_job(data_root: Path, key: str) -> dict | None:
    root = _root(data_root)
    folder = _dataset(root, key)
    if not _inside(root, folder / "job.json").is_file():
        return None
    folder = _managed_folder(root, key)
    with _lock(_inside(root, folder / ".job.lock")):
        return _refresh_job(folder, _read_json(_inside(root, folder / "job.json")))


def list_jobs(data_root: Path) -> list[dict]:
    """List current datasets; archived sources remain readable by their original key."""
    root = _root(data_root)
    if not root.is_dir():
        return []
    jobs = []
    for folder in root.iterdir():
        if not folder.is_dir() or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,100}", folder.name):
            continue
        try:
            job = get_job(root, folder.name)
            if job is not None and not job.get("archived_at"):
                jobs.append(job)
        except (ValueError, OSError):
            continue
    return sorted(jobs, key=lambda job: job["created_at"], reverse=True)


def start_index(data_root: Path, key: str, settings) -> dict:
    root = _root(data_root)
    folder = _managed_folder(root, key)
    with (
        _lock(_inside(root, ".ingestion.lock")),
        _lock(_inside(root, folder / ".job.lock")),
    ):
        job = _refresh_job(folder, _read_json(_inside(root, folder / "job.json")))
        if job["status"] in ACTIVE | {"succeeded"}:
            return job
        from .runtime import initialize, worker_environment

        manifest = _read_json(folder / "manifest.json")
        initialize(
            folder,
            settings,
            manifest["chunk_size"],
            manifest["overlap"],
            **({"profile": manifest["profile"]} if manifest.get("profile") else {}),
        )
        run_id = uuid.uuid4().hex
        job.update(
            status="queued",
            stage="启动索引进程",
            progress=0.0,
            error=None,
            attempt=job["attempt"] + 1,
            run_id=run_id,
            pid=None,
            launch_time=time.time(),
            updated_at=_now(),
        )
        _atomic_json(folder / "job.json", job)
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "bank_project.graphrag.worker",
                    "--data-root",
                    str(root),
                    "--key",
                    key,
                    "--run-id",
                    run_id,
                ],
                cwd=Path.cwd(),
                env=worker_environment(settings),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            _CHILDREN[str(folder)] = process
            job["pid"] = process.pid
        except OSError as exc:
            job.update(status="failed", stage="启动失败", error=redact_error(exc, folder))
        _atomic_json(folder / "job.json", job)
        return job


def _update_job(folder: Path, run_id: str, **changes: Any) -> dict:
    with _lock(_inside(folder, ".job.lock")):
        job = _read_json(_inside(folder, "job.json"))
        if job.get("run_id") != run_id:
            raise RuntimeError("索引任务已被新的重试任务替代。")
        job.update(changes, updated_at=_now())
        _atomic_json(folder / "job.json", job)
        return job


def secret_values(folder: Path) -> set[str]:
    values = {value for key, value in os.environ.items() if _SECRET_FIELD.search(key) and value}
    return values


def redact_error(error: Any, folder: Path, secrets: set[str] | None = None) -> str:
    message = str(error)
    for value in sorted(
        secrets if secrets is not None else secret_values(folder), key=len, reverse=True
    ):
        if len(value) >= 4:
            message = message.replace(value, "[REDACTED]")
    message = re.sub(r"(?i)(bearer\s+)[\w.+/=-]+", r"\1[REDACTED]", message)
    message = re.sub(
        r"(?i)(api[_-]?key|token|secret|password)([\s'\"]*[:=][\s'\"]*)[^\s,'\"}]+",
        r"\1\2[REDACTED]",
        message,
    )
    return message[:2000]


def _register(root: Path, key: str, *, community_level: int) -> None:
    folder = _managed_folder(root, key)
    manifest = _read_json(_inside(folder, "manifest.json"))
    with _lock(_inside(root, ".ingestion.lock")):
        entries = _listing(root)
        existing = next((entry for entry in entries if entry.get("key") == key), None)
        if existing is not None:
            if existing.get("path") != key:
                raise ValueError("数据集标识与现有索引冲突。")
            return
        entries.append(
            {
                "key": key,
                "path": key,
                "name": manifest["name"],
                "description": manifest["description"],
                "community_level": community_level,
            }
        )
        _atomic_json(_inside(root, "listing.json"), entries)
