"""Versioned source application profiles, separate from credentials and dataset facts."""

import hashlib
import json
from pathlib import Path

ENTERPRISE_TYPES = [
    "组织机构",
    "人物",
    "地点",
    "事件",
    "产品或技术",
    "项目或合同",
    "财务指标",
    "战略关键词",
]
PROFILES = {
    "enterprise_zh": {
        "id": "enterprise_zh",
        "name": "企业情报 · 原项目中文方案",
        "description": "原科大讯飞企业情报提示词、8 类实体、分步模型与检索参数；不注入企业事实。",
        "chunk_size": 1000,
        "overlap": 150,
        "entity_types": ENTERPRISE_TYPES,
    },
    "general": {
        "id": "general",
        "name": "通用文档 · 跟随原文语言",
        "description": "通用实体抽取，保留原文名称，适用于非企业语料。",
        "chunk_size": 1200,
        "overlap": 100,
        "entity_types": ["organization", "person", "geo", "event"],
    },
}


def profile_info(name):
    if name not in PROFILES:
        raise ValueError("未知的 GraphRAG 分析方案")
    return dict(PROFILES[name])


def apply_profile(folder, config, name, settings):
    profile_info(name)
    if name == "enterprise_zh":
        directory = Path(__file__).parent / "profiles" / name
        for prompt in directory.glob("*.txt"):
            (folder / "prompts" / prompt.name).write_bytes(prompt.read_bytes())
        config["extract_graph"].update(entity_types=ENTERPRISE_TYPES, max_gleanings=1)
        chat = config["models"]["default_chat_model"]
        chat.update(concurrent_requests=8, request_timeout=600, max_retries=3)
        config["models"]["fast_chat_model"] = {
            **chat,
            "model": getattr(settings, "graphrag_fast_chat_model", None)
            or ("qwen-flash" if settings.model_provider == "dashscope" else chat["model"]),
            "request_timeout": 300,
            "max_retries": 5,
        }
        config["models"]["default_embedding_model"].update(
            concurrent_requests=3, request_timeout=90, max_retries=10
        )
        config["summarize_descriptions"].update(
            model_id="fast_chat_model", max_length=500, max_input_tokens=16000
        )
        config["community_reports"].update(
            model_id="fast_chat_model", max_length=2000, max_input_length=8000
        )
        config["drift_search"].update(
            data_max_tokens=4000,
            reduce_max_tokens=800,
            concurrency=3,
            drift_k_followups=3,
            primer_folds=1,
            primer_llm_max_tokens=3000,
            n_depth=1,
            local_search_top_k_mapped_entities=5,
            local_search_top_k_relationships=5,
            local_search_max_data_tokens=4000,
            local_search_llm_max_gen_tokens=500,
        )
    config.setdefault("snapshots", {})["raw_graph"] = True
    prompts = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((folder / "prompts").glob("*.txt"))
    }
    (folder / "profile.json").write_text(
        json.dumps({**profile_info(name), "prompt_sha256": prompts}, ensure_ascii=False, indent=2)
    )
