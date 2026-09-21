"""GraphRAG 2.5 configuration adapter; all model credentials come from BANK settings."""

from __future__ import annotations

import copy
import importlib.metadata
import os
from pathlib import Path


class GraphRagError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def dependency_version() -> str | None:
    try:
        return importlib.metadata.version("graphrag")
    except importlib.metadata.PackageNotFoundError:
        return None


def require_runtime() -> None:
    if dependency_version() != "2.5.0":
        raise GraphRagError(
            "请安装 GraphRAG 运行依赖：pip install '.[graphrag]'（固定 2.5.0）", 503
        )


def _secret(value) -> str:
    return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value or "")


def model_parameters(settings) -> dict:
    chat_key = _secret(getattr(settings, "graphrag_api_key", None) or settings.model_api_key)
    chat_base = getattr(settings, "graphrag_api_base", None) or settings.model_base_url
    if not chat_base:
        chat_base = (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
            if settings.model_provider == "dashscope"
            else "https://api.openai.com/v1"
        )
    chat_model = getattr(settings, "graphrag_chat_model", None) or settings.model_name
    if not chat_model and settings.model_provider == "dashscope":
        chat_model = "qwen3.7-plus"
    embedding_model = (
        getattr(settings, "graphrag_embedding_model", None) or "text-embedding-3-small"
    )
    return {
        "chat_model": chat_model,
        "chat_base": chat_base,
        "chat_key": chat_key,
        "embedding_model": embedding_model,
        "embedding_base": getattr(settings, "graphrag_embedding_api_base", None) or chat_base,
        "embedding_key": _secret(getattr(settings, "graphrag_embedding_api_key", None)) or chat_key,
    }


def require_models(settings) -> dict:
    params = model_parameters(settings)
    if not all(params.values()):
        raise GraphRagError("请配置 GraphRAG 聊天模型、向量模型以及各自的服务地址和密钥", 503)
    return params


def initialize(folder: Path, settings, chunk_size: int, overlap: int, profile="general") -> None:
    """Generate native indexing/search config with source-language prompt rules."""
    require_runtime()
    params = require_models(settings)
    import yaml
    from graphrag.cli.initialize import initialize_project_at

    from .prompts import configure_prompt_languages

    initialize_project_at(folder, force=True)
    configure_prompt_languages(folder)
    config = yaml.safe_load((folder / "settings.yaml").read_text(encoding="utf-8"))
    # The upstream initializer writes a placeholder .env. Never load it or store secrets there.
    (folder / ".env").unlink(missing_ok=True)
    common = {
        "api_key": "",
        "model_supports_json": True,
        # Compatible providers (Qwen etc.) are absent from tiktoken's model-name map.
        "encoding_model": "cl100k_base",
        "request_timeout": getattr(settings, "graphrag_request_timeout_seconds", 600),
        # Compatible providers may omit OpenAI rate-limit headers. Native "auto"
        # then stays at one request/token per minute; reuse the source app's
        # explicit unlimited rate policy, bounded by concurrent_requests instead.
        "tokens_per_minute": None,
        "requests_per_minute": None,
        # GraphRAG 2.5 rejects zero, while the existing transport allows it.
        "max_retries": max(1, settings.model_max_retries),
        "concurrent_requests": settings.alignment_model_concurrency,
    }
    config["models"] = {
        "default_chat_model": {
            **common,
            "type": "openai_chat",
            "model": params["chat_model"],
            "api_base": params["chat_base"],
        },
        "default_embedding_model": {
            **common,
            "type": "openai_embedding",
            "model": params["embedding_model"],
            "api_base": params["embedding_base"],
        },
    }
    config["input"] = {
        "storage": {"type": "file", "base_dir": "input"},
        "file_type": "csv",
        "encoding": "utf-8",
        "file_pattern": r"documents\.csv$",
        "text_column": "text",
        "title_column": "title",
        "metadata": ["source_name", "source_sha256"],
    }
    config["chunks"] = {"size": chunk_size, "overlap": overlap, "group_by_columns": ["id"]}
    for section in ("output", "cache", "reporting"):
        config[section] = {
            "type": "file",
            "base_dir": "logs" if section == "reporting" else section,
        }
    config["vector_store"] = {
        "default_vector_store": {
            "type": "lancedb",
            "db_uri": "output/lancedb",
            "container_name": "default",
            "overwrite": True,
        }
    }
    config["embed_text"]["vector_store_id"] = "default_vector_store"
    config["embed_text"]["names"] = [
        "entity.description",
        "community.full_content",
        "text_unit.text",
    ]
    config["drift_search"]["reduce_prompt"] = "prompts/drift_reduce_prompt.txt"
    from .profiles import apply_profile

    apply_profile(folder, config, profile, settings)
    config.pop("workflows", None)
    (folder / "settings.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    # Validate the complete native config before a job is reported as started.
    load_config(folder, settings)


def load_config(folder: Path, settings):
    """Inject secrets into a fresh config object without modifying process environment."""
    require_runtime()
    params = require_models(settings)
    import yaml
    from graphrag.config.create_graphrag_config import create_graphrag_config

    from .jobs import _inside

    config = yaml.safe_load(_inside(folder, "settings.yaml").read_text(encoding="utf-8"))
    config = copy.deepcopy(config)
    # Model names/addresses are pinned with the index; credentials are supplied at runtime.
    for model in config["models"].values():
        model["api_key"] = (
            params["embedding_key"] if model["type"] == "openai_embedding" else params["chat_key"]
        )
    for store in config.get("vector_store", {}).values():
        store["db_uri"] = str(_inside(folder, store["db_uri"]))
        # Query must open existing vectors. build_index explicitly controls overwrites itself.
    try:
        return create_graphrag_config(config, root_dir=str(folder.resolve()))
    except (ValueError, TypeError, KeyError) as exc:
        # Native Pydantic errors include the input model dict, including its API key.
        raise GraphRagError("GraphRAG 模型或索引配置无效，请检查服务端配置", 503) from exc


def worker_environment(settings) -> dict[str, str]:
    """Pass explicit Settings values to the detached worker, including in-process overrides."""
    params = require_models(settings)
    environment = dict(os.environ)
    values = {
        "DATA_DIR": str(settings.data_dir.resolve()),
        "MODEL_PROVIDER": settings.model_provider,
        "MODEL_TIMEOUT_SECONDS": settings.model_timeout_seconds,
        "MODEL_MAX_RETRIES": settings.model_max_retries,
        "ALIGNMENT_MODEL_CONCURRENCY": settings.alignment_model_concurrency,
        "GRAPHRAG_CHAT_MODEL": params["chat_model"],
        "GRAPHRAG_FAST_CHAT_MODEL": getattr(settings, "graphrag_fast_chat_model", None) or "",
        "GRAPHRAG_API_BASE": params["chat_base"],
        "GRAPHRAG_API_KEY": params["chat_key"],
        "GRAPHRAG_EMBEDDING_MODEL": params["embedding_model"],
        "GRAPHRAG_EMBEDDING_API_BASE": params["embedding_base"],
        "GRAPHRAG_EMBEDDING_API_KEY": params["embedding_key"],
        "GRAPHRAG_REQUEST_TIMEOUT_SECONDS": getattr(
            settings, "graphrag_request_timeout_seconds", 600
        ),
    }
    environment.update({f"BANK_{key}": str(value) for key, value in values.items()})
    # Support source-checkout execution as well as an installed wheel.
    source_root = str(Path(__file__).resolve().parents[2])
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [source_root, environment.get("PYTHONPATH")])
    )
    return environment
