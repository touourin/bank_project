# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License; adapted from GraphRAG Search (see LICENSE).

"""Detached indexing process launched by the durable GraphRAG jobs service."""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import logging
import os
import time
import traceback
from pathlib import Path
from typing import Any

from . import jobs as service


async def _load_canonical_csv(config: Any, storage: Any):
    """Keep literal 'NA', numeric titles and source names as strings in pandas.

    GraphRAG 2.5's general CSV reader infers numbers/nulls. The import format is
    already validated, so this worker uses a strict reader for its own CSV only.
    GraphRAG's input factory still attaches metadata normally.
    """
    import pandas as pd

    raw = await storage.get("documents.csv", as_bytes=True)
    # A fresh worker has csv's 128 KiB default field limit, while validated
    # uploads can contain much larger individual document bodies.
    csv.field_size_limit(max(csv.field_size_limit(), len(raw)))
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    fields = ["id", "title", "text", "source_name", "source_sha256"]
    if reader.fieldnames != fields:
        raise ValueError("导入文档表字段不完整。")
    rows = list(reader)
    if not rows or any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("导入文档表内容不完整。")
    frame = pd.DataFrame(rows, columns=fields, dtype=str)
    frame["creation_date"] = await storage.get_creation_date("documents.csv")
    return frame


class JobProgress:
    """Persist pipeline progress without storing prompts or model credentials."""

    def __init__(self, folder: Path, run_id: str):
        self.folder = folder
        self.run_id = run_id
        self.names: list[str] = []
        self.completed: set[str] = set()
        self.current = "初始化"
        self.last_write = 0.0

    def pipeline_start(self, names: list[str]) -> None:
        self.names = names
        self._write(0.0)

    def pipeline_end(self, results: list[Any]) -> None:
        # Completion is only committed after artifact validation and registration.
        self.current = "验证索引产物"
        self._write(0.98)

    def workflow_start(self, name: str, instance: Any) -> None:
        self.current = name
        self._write(self._fraction())

    def workflow_end(self, name: str, instance: Any) -> None:
        self.completed.add(name)
        self._write(self._fraction())

    def progress(self, progress: Any) -> None:
        if time.monotonic() - self.last_write < 0.25:
            return
        fraction = getattr(progress, "percent", None)
        total = getattr(progress, "total_items", None)
        done = getattr(progress, "completed_items", None)
        if fraction is None and total and done is not None:
            fraction = done / total
        self._write(self._fraction(min(1.0, max(0.0, fraction or 0.0))))

    def _fraction(self, partial: float = 0.0) -> float:
        return min(0.97, 0.97 * (len(self.completed) + partial) / max(1, len(self.names)))

    def _write(self, fraction: float) -> None:
        service._update_job(self.folder, self.run_id, stage=self.current, progress=fraction)
        self.last_write = time.monotonic()


def _verify_artifacts(folder: Path) -> dict:
    """Read the actual parquet/vector outputs before exposing them to query UI."""
    import lancedb
    import pandas as pd
    from graphrag.data_model import schemas

    required = {
        "documents": set(schemas.DOCUMENTS_FINAL_COLUMNS),
        "text_units": set(schemas.TEXT_UNITS_FINAL_COLUMNS),
        "entities": set(schemas.ENTITIES_FINAL_COLUMNS),
        "relationships": set(schemas.RELATIONSHIPS_FINAL_COLUMNS),
        "communities": set(schemas.COMMUNITIES_FINAL_COLUMNS),
        "community_reports": set(schemas.COMMUNITY_REPORTS_FINAL_COLUMNS),
    }
    frames = {}
    for name, columns in required.items():
        path = service._inside(folder, f"output/{name}.parquet")
        if not path.is_file():
            raise ValueError(f"缺少索引产物：{name}.parquet")
        frame = pd.read_parquet(path)
        if frame.empty or not columns.issubset(frame.columns):
            raise ValueError(f"索引产物为空或字段不完整：{name}.parquet")
        frames[name] = frame
    manifest = service._read_json(service._inside(folder, "manifest.json"))
    expected = {document["id"] for document in manifest["documents"]}
    if set(frames["documents"]["id"].astype(str)) != expected:
        raise ValueError("索引中的文档与本次导入不一致。")
    expected_sources = {document["id"]: document for document in manifest["documents"]}
    for document in frames["documents"].to_dict("records"):
        metadata = document["metadata"]
        if not isinstance(metadata, dict) or any(
            metadata.get(field) != expected_sources[document["id"]][field]
            for field in ("source_name", "source_sha256")
        ):
            raise ValueError("索引文档丢失了原始来源信息。")
    referenced = {
        str(identifier) for ids in frames["text_units"]["document_ids"] for identifier in ids
    }
    if referenced != expected:
        raise ValueError("部分导入文档没有生成可追溯的文本块。")
    database_path = service._inside(folder, "output/lancedb")
    if not database_path.is_dir():
        raise ValueError("缺少向量索引。")
    database = lancedb.connect(str(database_path))
    names = set(database.table_names())
    vectors = {}
    vector_sources = {
        "default-entity-description": "entities",
        "default-community-full_content": "community_reports",
        "default-text_unit-text": "text_units",
    }
    for name, source in vector_sources.items():
        if name not in names:
            raise ValueError(f"缺少向量表：{name}")
        table = database.open_table(name)
        count = table.count_rows()
        if count != len(frames[source]) or not {"id", "text", "vector"}.issubset(
            table.schema.names
        ):
            raise ValueError(f"向量表为空或字段不完整：{name}")
        vectors[name] = count
    return {
        "tables": {name: len(frame) for name, frame in frames.items()},
        "vectors": vectors,
        "community_level": int(frames["community_reports"]["level"].min()),
    }


def _check_paths(folder: Path, config: Any) -> None:
    """Reject modified configurations that would write outside this new dataset."""
    for name in ("output", "cache", "reporting", "update_index_output"):
        storage = getattr(config, name)
        if str(storage.type) not in {
            "file",
            "StorageType.file",
            "CacheType.file",
            "ReportingType.file",
        }:
            raise ValueError("文档导入索引仅支持本地文件存储。")
        service._inside(folder, storage.base_dir)
    for storage in config.vector_store.values():
        if str(storage.type) != "lancedb":
            raise ValueError("文档导入索引仅支持本地 LanceDB。")
        service._inside(folder, storage.db_uri)
    service._inside(folder, config.input.storage.base_dir)
    if config.workflows:
        raise ValueError("文档导入索引不能使用自定义工作流。")


def _redacted_logging(folder: Path):
    previous = logging.getLogRecordFactory()
    secrets = service.secret_values(folder)

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        record.msg = service.redact_error(record.getMessage(), folder, secrets)
        record.args = ()
        if record.exc_info:
            record.exc_text = service.redact_error(
                "".join(traceback.format_exception(*record.exc_info)), folder, secrets
            )
            record.exc_info = None
        return record

    logging.setLogRecordFactory(factory)
    return previous


def run_job(data_root: Path, key: str, run_id: str) -> int:
    """Execute one attempt; failures remain retryable and are never registered."""
    root = service._root(data_root)
    folder = service._managed_folder(root, key)
    try:
        with service._lock(service._inside(folder, ".run.lock"), blocking=False):
            job = service._read_json(service._inside(folder, "job.json"))
            if job.get("run_id") != run_id or job.get("status") not in service.ACTIVE:
                return 0
            previous_factory = None
            previous_loader = None
            try:
                service._update_job(
                    folder,
                    run_id,
                    status="running",
                    stage="加载模型配置",
                    pid=os.getpid(),
                )
                previous_factory = _redacted_logging(folder)
                # Imports happen after the durable lock/state update, so a cold
                # Python startup remains visible and cannot launch a second job.
                from graphrag.api import build_index
                from graphrag.config.enums import InputFileType
                from graphrag.index.input.factory import loaders

                from bank_project.settings import Settings

                from .runtime import load_config

                config = load_config(folder, Settings(_env_file=None))
                _check_paths(folder, config)
                previous_loader = loaders[InputFileType.csv]
                loaders[InputFileType.csv] = _load_canonical_csv
                callbacks = JobProgress(folder, run_id)
                from .records import capture_records

                with capture_records(folder):
                    results = asyncio.run(build_index(config=config, callbacks=[callbacks]))
                if not results:
                    raise RuntimeError("索引流程没有返回执行结果。")
                errors = [
                    f"{result.workflow}: {error}"
                    for result in results
                    for error in (result.errors or [])
                ]
                if errors:
                    raise RuntimeError("\n".join(errors))
                service._update_job(folder, run_id, stage="验证索引产物", progress=0.98)
                artifacts = _verify_artifacts(folder)
                artifacts["resolution_records"] = (
                    folder / "output/entity_resolution/latest.json"
                ).is_file()
                if artifacts["resolution_records"]:
                    from .records import load_records

                    artifacts["resolution_record_count"] = len(load_records(folder).mentions)
                service._register(root, key, community_level=artifacts["community_level"])
                service._update_job(
                    folder,
                    run_id,
                    status="succeeded",
                    stage="索引完成",
                    progress=1.0,
                    error=None,
                    pid=None,
                    artifacts=artifacts,
                    finished_at=service._now(),
                )
                return 0
            except Exception as exc:
                service._update_job(
                    folder,
                    run_id,
                    status="failed",
                    stage="索引失败",
                    pid=None,
                    error=service.redact_error(exc, folder),
                    finished_at=service._now(),
                )
                return 1
            finally:
                if previous_loader is not None:
                    loaders[InputFileType.csv] = previous_loader
                if previous_factory is not None:
                    logging.setLogRecordFactory(previous_factory)
    except BlockingIOError:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    return run_job(args.data_root, args.key, args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
