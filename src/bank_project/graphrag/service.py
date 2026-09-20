"""Framework-independent facade over migrated GraphRAG ingestion and query code."""

from __future__ import annotations

import asyncio
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import jobs
from .parsers import MAX_FILE_BYTES, parse_uploads
from .runtime import (
    GraphRagError,
    dependency_version,
    load_config,
    model_parameters,
    require_runtime,
)

# GraphRAG 2.5 caches query models under fixed process-global names. Its API has
# no manager argument; serialize calls and discard only query-owned entries so
# different datasets/providers never reuse the first dataset's model client.
_NATIVE_QUERY_LOCK = asyncio.Lock()


def _clear_query_models() -> None:
    from graphrag.language_model.manager import ModelManager

    manager = ModelManager()
    for name in ("local_search_chat", "global_search", "drift_search_chat"):
        manager.remove_chat(name)
    for name in ("local_search_embedding", "drift_search_embedding"):
        manager.remove_embedding(name)


def json_value(value: Any) -> Any:
    """Convert native parquet/vector context values without dropping any fields."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "to_dict"):
        return json_value(value.to_dict(orient="records"))
    if hasattr(value, "tolist"):
        return json_value(value.tolist())
    if hasattr(value, "item"):
        return json_value(value.item())
    # pandas scalar missing values cannot be tested with a boolean comparison.
    if type(value).__name__ in {"NAType", "NaTType"}:
        return None
    return str(value)


class GraphRagService:
    def __init__(self, settings):
        self.settings = settings
        self.root = Path(settings.data_dir).resolve() / "graphrag"

    def config(self) -> dict:
        version = dependency_version()
        model_configured = all(model_parameters(self.settings).values())
        runtime_available = version == "2.5.0"
        error = None
        if not runtime_available:
            error = "未安装 GraphRAG 2.5.0 运行依赖；仍可保存 TXT，配置完成后建立索引。"
        elif not model_configured:
            error = "尚未配置聊天或向量模型；仍可保存 TXT，配置完成后建立索引。"
        return {
            "configured": runtime_available and model_configured,
            "model_configured": model_configured,
            "runtime_available": runtime_available,
            "runtime_version": version,
            "error": error,
            "max_upload_bytes": MAX_FILE_BYTES,
            "supported_extensions": ["txt"],
            "methods": ["local", "global", "drift"],
        }

    def list_datasets(self) -> list[dict]:
        return jobs.list_jobs(self.root)

    def dataset(self, key: str) -> dict:
        try:
            result = jobs.get_job(self.root, key)
        except (OSError, ValueError) as exc:
            raise GraphRagError("数据集不存在或不可读取", 404) from exc
        if result is None:
            raise GraphRagError("数据集不存在", 404)
        return result

    def upload(self, content: bytes, filename: str, name: str | None = None) -> dict:
        if Path(filename).suffix.lower() != ".txt":
            raise GraphRagError("文本文档接入支持 .txt；Excel/CSV 请使用表格接入")
        documents = parse_uploads([(filename, content)])
        try:
            key = jobs.create_dataset(
                self.root,
                name=name or Path(filename).stem,
                description="TXT 文档 · GraphRAG",
                documents=documents,
                chunk_size=getattr(self.settings, "graphrag_chunk_size", 1200),
                overlap=getattr(self.settings, "graphrag_chunk_overlap", 100),
            )
        except ValueError as exc:
            # This path only validates/saves local documents; it never loads model config.
            raise GraphRagError(str(exc)) from exc
        return self.dataset(key)

    def start_index(self, key: str) -> dict:
        self.dataset(key)
        try:
            return jobs.start_index(self.root, key, self.settings)
        except GraphRagError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            # Native initialization and Pydantic errors may embed plaintext model keys.
            raise GraphRagError("GraphRAG 索引配置无效，请检查服务端模型配置后重试", 503) from exc

    def _indexed_folder(self, key: str) -> tuple[dict, Path]:
        job = self.dataset(key)
        if job["status"] != "succeeded":
            raise GraphRagError("数据集尚未完成索引，请先开始索引或重试失败任务", 409)
        return job, jobs._managed_folder(self.root, key)

    def graph(self, key: str) -> dict:
        job, folder = self._indexed_folder(key)
        require_runtime()
        import pandas as pd

        entities = json_value(pd.read_parquet(jobs._inside(folder, "output/entities.parquet")))
        relationships = json_value(
            pd.read_parquet(jobs._inside(folder, "output/relationships.parquet"))
        )
        text_units = json_value(pd.read_parquet(jobs._inside(folder, "output/text_units.parquet")))
        source_texts = {}
        for unit in text_units:
            unit_id = unit.get("id")
            if not isinstance(unit_id, str) or not unit_id or unit_id in source_texts:
                raise GraphRagError("GraphRAG 原文分块 ID 缺失或重复，无法唯一定位源证据", 409)
            if not isinstance(unit.get("text"), str) or not unit["text"].strip():
                raise GraphRagError("GraphRAG 原文分块正文缺失，源证据不完整", 409)
            source_texts[unit_id] = unit["text"]

        def evidence_ids(record: dict) -> list[str]:
            values = record.get("text_unit_ids")
            if values is None:
                # An explicit absence remains visible; it is not claimed as source evidence.
                return []
            if not isinstance(values, list) or any(
                not isinstance(value, str) or value not in source_texts for value in values
            ):
                raise GraphRagError("GraphRAG 实体或关系引用的原文分块缺失，源证据不完整", 409)
            return list(dict.fromkeys(values))

        nodes = []
        by_title = {}
        ids = set()
        for entity in entities:
            identifier = str(entity["id"])
            title = str(entity["title"])
            if identifier in ids or title in by_title:
                raise GraphRagError("GraphRAG 实体 ID 或名称重复，无法唯一确定关系端点", 409)
            ids.add(identifier)
            by_title[title] = identifier
            unit_ids = evidence_ids(entity)
            nodes.append(
                {
                    "id": identifier,
                    "name": title,
                    "type": entity.get("type") or "UNKNOWN",
                    "properties": entity,
                    "source_context": "\n\n".join(source_texts[unit_id] for unit_id in unit_ids),
                    "source_text_unit_ids": unit_ids,
                    "source_evidence_status": "available" if unit_ids else "missing",
                }
            )
        edges = []
        edge_ids = set()
        for relation in relationships:
            relation_unit_ids = evidence_ids(relation)
            source = by_title.get(str(relation["source"]))
            target = by_title.get(str(relation["target"]))
            if source is None or target is None:
                raise GraphRagError("GraphRAG 关系端点无法定位，索引产物不完整", 409)
            identifier = str(relation["id"])
            if identifier in edge_ids:
                raise GraphRagError("GraphRAG 关系 ID 重复", 409)
            edge_ids.add(identifier)
            edges.append(
                {
                    "id": identifier,
                    "source": source,
                    "target": target,
                    "properties": relation,
                    "source_text_unit_ids": relation_unit_ids,
                    "source_evidence_status": "available" if relation_unit_ids else "missing",
                }
            )
        return {
            "id": key,
            "name": job["name"],
            "source_kind": "graphrag",
            "source_id": key,
            "nodes": nodes,
            "edges": edges,
            "metadata": {
                "index_basis": "original_graphrag_index",
                "node_count": len(nodes),
                "edge_count": len(edges),
            },
        }

    def _query_inputs(self, key: str) -> tuple[Any, dict, int]:
        job, folder = self._indexed_folder(key)
        config = load_config(folder, self.settings)
        from .worker import _check_paths

        _check_paths(folder, config)
        import pandas as pd

        tables = {
            name: pd.read_parquet(jobs._inside(folder, f"output/{name}.parquet"))
            for name in (
                "entities",
                "relationships",
                "communities",
                "community_reports",
                "text_units",
            )
        }
        covariates = jobs._inside(folder, "output/covariates.parquet")
        tables["covariates"] = pd.read_parquet(covariates) if covariates.is_file() else None
        level = int(
            job.get("artifacts", {}).get(
                "community_level", tables["community_reports"]["level"].min()
            )
        )
        return config, tables, level

    async def query(self, key: str, question: str, method: str = "local") -> dict:
        question = question.strip()
        if not question or len(question) > 16000:
            raise GraphRagError("问题需为 1–16000 个字符")
        if method not in {"local", "global", "drift"}:
            raise GraphRagError("不支持的 GraphRAG 检索方式")
        # The public API and complete dataframes match the migrated GraphRAG Search app.
        async with _NATIVE_QUERY_LOCK:
            config, tables, level = await asyncio.to_thread(self._query_inputs, key)
            import graphrag.api as api

            common = {
                "config": config,
                "entities": tables["entities"],
                "communities": tables["communities"],
                "community_reports": tables["community_reports"],
                "community_level": level,
                "response_type": "Multiple Paragraphs",
                "query": question,
            }
            _clear_query_models()
            try:
                if method == "global":
                    answer, context = await api.global_search(
                        **common, dynamic_community_selection=False
                    )
                else:
                    common.update(
                        text_units=tables["text_units"], relationships=tables["relationships"]
                    )
                    if method == "local":
                        answer, context = await api.local_search(
                            **common, covariates=tables["covariates"]
                        )
                    else:
                        answer, context = await api.drift_search(**common)
            except Exception as exc:
                # Provider errors can embed credentials or input excerpts. Keep them out of HTTP responses.
                raise GraphRagError(
                    "GraphRAG 问答未完成，请检查模型配置、索引产物和服务状态后重试", 503
                ) from exc
            finally:
                _clear_query_models()
        return {
            "answer": answer
            if isinstance(answer, str)
            else json.dumps(json_value(answer), ensure_ascii=False),
            "context": json_value(context),
            "method": method,
            "dataset_key": key,
            "index_basis": "original_graphrag_index",
        }
