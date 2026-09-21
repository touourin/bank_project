"""Read paired experiment artifacts without importing a different GraphRAG runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

METHODS = ("legacy_title_v1", "evidence_v1")
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_RUN_BYTES = 192 * 1024 * 1024


def _read(path: Path, limit: int = MAX_FILE_BYTES) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError(f"文件过大，超出页面读取上限：{path.name}")
    return content


def _object(raw: bytes, name: str) -> dict:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError(f"不是有效的 JSON 对象：{name}")
    return value


def list_experiments(root: Path) -> tuple[list[dict], list[str]]:
    """List immediate child runs, retaining incomplete states and parse errors."""
    runs, errors = [], []
    if not root.is_dir():
        return runs, errors
    for directory in root.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        path = directory / "run.json"
        if not path.is_file():
            continue
        try:
            if not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("实验清单超出结果目录")
            manifest = _object(_read(path, 2 * 1024 * 1024), "run.json")
            if manifest.get("schema_version") != "er-run-v1":
                raise ValueError("不支持的实验清单版本")
            runs.append(
                {
                    "name": directory.name,
                    "state": manifest.get("state", "unknown"),
                    "created_at": str(manifest.get("created_at", "")),
                }
            )
        except (OSError, ValueError, TypeError) as exc:
            errors.append(f"{directory.name}：{exc}")
    return sorted(runs, key=lambda item: (item["created_at"], item["name"]), reverse=True), errors


@dataclass
class Experiment:
    """Verified viewer inputs and indexes used by both method panels."""

    name: str
    manifest: dict
    report: dict
    corpus: dict
    results: dict
    differences: list[dict]
    gold: dict | None
    config: dict
    artifacts: dict[str, bytes]

    @property
    def methods(self) -> tuple[str, str]:
        methods = self.report.get("methods", METHODS)
        return methods[0], methods[1]

    @property
    def mentions(self) -> dict[str, dict]:
        return {row["mention_id"]: row for row in self.corpus["mentions"]}

    def memberships(self, method: str) -> dict[str, dict]:
        return {row["mention_id"]: row for row in self.results[method]["memberships"]}


def load_experiment(root: Path, name: str) -> Experiment:
    """Reject partial, mixed-input or modified artifacts before showing any scores."""
    root = root.resolve()
    directory = (root / name).resolve()
    if not name or Path(name).name != name or directory.parent != root:
        raise ValueError("无效的实验目录")
    manifest_path = directory / "run.json"
    if not manifest_path.resolve().is_relative_to(directory):
        raise ValueError("实验清单超出结果目录")
    manifest = _object(_read(manifest_path, 2 * 1024 * 1024), "run.json")
    if manifest.get("schema_version") != "er-run-v1":
        raise ValueError("不支持的实验清单版本")
    if manifest.get("state") != "complete":
        raise ValueError("实验尚未完成，不能展示为完整对比结果")
    methods = tuple(manifest.get("methods", METHODS))
    if methods not in (METHODS, ("legacy_title_v1", "synonym_llm_v1")):
        raise ValueError("不支持的对比方法组合")
    hashes = manifest.get("artifact_sha256", {})
    if not isinstance(hashes, dict):
        raise TypeError("实验缺少产物校验清单")
    names = [
        "comparison.json",
        "corpus.json",
        "resolver_config.json",
        "differences.jsonl",
        "comparison.md",
        "annotation_template.json",
        *(f"{method}/result.json" for method in methods),
    ]
    if methods[1] == "synonym_llm_v1":
        names.extend(["alias_expansion.json", "synonyms.proposals.json"])
        if manifest.get("synonyms_sha256") is not None:
            names.append("synonyms.input.json")
    if manifest.get("gold_sha256") is not None:
        names.append("gold.json")
    names = list(dict.fromkeys([*names, *hashes]))
    artifacts, total = {}, 0
    for artifact in names:
        path = (directory / artifact).resolve()
        if not path.is_relative_to(directory):
            raise ValueError(f"产物路径超出结果目录：{artifact}")
        raw = _read(path)
        total += len(raw)
        if total > MAX_RUN_BYTES:
            raise ValueError("该实验超出页面读取上限，请先使用较小的评测批次")
        if hashlib.sha256(raw).hexdigest() != hashes.get(artifact):
            raise ValueError(f"产物校验失败：{artifact}。文件可能缺失、被修改或来自另一轮实验。")
        artifacts[artifact] = raw
    report = _object(artifacts["comparison.json"], "comparison.json")
    corpus = _object(artifacts["corpus.json"], "corpus.json")
    config = _object(artifacts["resolver_config.json"], "resolver_config.json")
    results = {method: _object(artifacts[f"{method}/result.json"], method) for method in methods}
    differences = [
        json.loads(line) for line in artifacts["differences.jsonl"].splitlines() if line.strip()
    ]
    gold = _object(artifacts["gold.json"], "gold.json") if "gold.json" in artifacts else None
    try:
        if tuple(report.get("methods", METHODS)) != methods:
            raise ValueError("实验清单和报告的方法不一致")
        if (
            report["schema_version"] != "er-comparison-v1"
            or corpus["schema_version"] != "er-corpus-v1"
        ):
            raise ValueError("不支持的实验数据版本")
        if any(
            item["corpus_sha256"] != manifest["corpus_sha256"]
            for item in (report, *results.values())
        ):
            raise ValueError("两个方法没有使用相同的输入快照")
        mids = [row["mention_id"] for row in corpus["mentions"]]
        if len(set(mids)) != len(mids) or report["records"] != len(mids):
            raise ValueError("输入记录数量或唯一性不一致")
        for method, result in results.items():
            members = [row["mention_id"] for row in result["memberships"]]
            if result["method"] != method or set(members) != set(mids) or len(members) != len(mids):
                raise ValueError("方法结果没有完整覆盖输入记录")
            entity_ids = {row["entity_id"] for row in result["entities"]}
            if any(
                row["entity_id"] is not None and row["entity_id"] not in entity_ids
                for row in result["memberships"]
            ):
                raise ValueError("记录归属引用了不存在的实体")
        if any(row["mention_id"] not in set(mids) for row in differences):
            raise ValueError("差异记录不属于当前输入")
        if len(differences) != report["changed_records"]:
            raise ValueError("差异记录数量不一致")
        measured = report["evaluation_status"] == "MEASURED_ON_PROVIDED_GOLD"
        if measured:
            if (
                gold is None
                or gold["corpus_sha256"] != manifest["corpus_sha256"]
                or not report["metrics"]
            ):
                raise ValueError("已评分报告缺少对应人工金标")
        elif report["evaluation_status"] != "NOT_MEASURED_NO_GOLD" or report["metrics"] is not None:
            raise ValueError("评测状态与指标内容不一致")
    except (KeyError, TypeError) as exc:
        raise ValueError("实验产物结构不完整，请重新导出对比结果") from exc
    return Experiment(name, manifest, report, corpus, results, differences, gold, config, artifacts)


def record_rows(experiment: Experiment) -> list[dict]:
    """Join records by immutable IDs, not by potentially ambiguous names."""
    changed = {row["mention_id"] for row in experiment.differences}
    old, new = (experiment.memberships(method) for method in experiment.methods)
    sizes = {
        method: {
            entity["entity_id"]: len(entity["mention_ids"])
            for entity in experiment.results[method]["entities"]
        }
        for method in experiment.methods
    }
    return [
        {
            "记录 ID": mid,
            "名称": mention["name"],
            "类型": mention.get("type", ""),
            "来源": mention.get("source_id", ""),
            "归属变化": "有变化" if mid in changed else "未变化",
            "旧簇大小": sizes[experiment.methods[0]].get(old[mid]["entity_id"], 0),
            "新簇大小": sizes[experiment.methods[1]].get(new[mid]["entity_id"], 0),
            "旧状态": old[mid]["status"],
            "新状态": new[mid]["status"],
        }
        for mid, mention in experiment.mentions.items()
    ]
