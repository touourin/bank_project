"""Enrich an ontology export from revision-checked WHY responses, without publishing it."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from bank_project.alignment.models import AlignmentError
from bank_project.settings import Settings

from .catalog import RiskCatalog
from .propagation import canonical_json

MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024


def _base_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
        valid = (
            parts.scheme in {"http", "https"}
            and parts.hostname
            and not parts.username
            and not parts.password
            and not parts.query
            and not parts.fragment
            and not parts.path.rstrip("/").endswith(("/retrieve", "/concept/dimensions"))
        )
        # Access validates malformed/out-of-range ports as well.
        _ = parts.port
    except (ValueError, AttributeError) as exc:
        raise AlignmentError("本体服务地址格式无效") from exc
    if not valid:
        raise AlignmentError("本体服务需要 HTTP(S) 根路径，不能含凭据、查询参数或片段")
    return value.strip().rstrip("/")


def _read_json(path: Path) -> Any:
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise AlignmentError("WHY 离线文件超过 30 MiB 上限")
        return json.loads(content)
    except (OSError, ValueError) as exc:
        raise AlignmentError("无法读取有效的 WHY 离线 JSON 文件") from exc


def _response(value: Any, node_id: str, revision: str) -> tuple[Any, str | None]:
    if (
        not isinstance(value, dict)
        or value.get("node_id") != node_id
        or value.get("dataset_revision") != revision
    ):
        raise AlignmentError("WHY 响应节点或本体版本与固定快照不一致")
    dimensions, hashes = value.get("dimensions"), value.get("dimension_hashes")
    if not isinstance(dimensions, dict) or "why" not in dimensions or not isinstance(hashes, dict):
        raise AlignmentError("WHY 响应缺少 dimensions.why 或 dimension_hashes")
    why = dimensions["why"]
    if why is not None and not isinstance(why, (dict, list)):
        raise AlignmentError("WHY 原文必须是 JSON 对象、数组或空值")
    try:
        digest = hashlib.sha256(canonical_json(why).encode()).hexdigest() if why else None
    except (ValueError, TypeError) as exc:
        raise AlignmentError("WHY 原文包含无效 JSON 数据") from exc
    if hashes.get("why") != digest:
        raise AlignmentError("WHY 原文哈希缺失或与维度哈希不一致")
    return deepcopy(why), digest


async def _fetch(
    client: httpx.AsyncClient, base_url: str, node_id: str, revision: str, timeout: float
):
    try:
        async with asyncio.timeout(timeout):
            async with client.stream(
                "POST",
                base_url + "/concept/dimensions",
                json={"node_id": node_id, "dimensions": ["why"], "expected_revision": revision},
            ) as response:
                if response.status_code != 200:
                    raise AlignmentError(
                        f"WHY 服务返回 HTTP {response.status_code}，未生成输出文件"
                    )
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise AlignmentError("单节点 WHY 响应超过 1 MiB，未生成输出文件")
        return json.loads(content)
    except (httpx.HTTPError, TimeoutError) as exc:
        raise AlignmentError("WHY 服务请求失败或超时，未生成输出文件") from exc
    except (ValueError, TypeError) as exc:
        raise AlignmentError("WHY 服务返回无效 JSON，未生成输出文件") from exc


def _apply(properties: dict, why: Any, digest: str | None) -> None:
    # Update existing projections too, so replay detects no contradictory copies.
    properties["why"] = why
    for field in ("dimensions", "description"):
        container = properties.get(field)
        if isinstance(container, str):
            container = json.loads(container)
        if isinstance(container, dict):
            if "why" in container:
                container["why"] = why
            nested = container.get("dimensions")
            if isinstance(nested, str):
                nested = json.loads(nested)
            if isinstance(nested, dict) and "why" in nested:
                nested["why"] = why
                container["dimensions"] = nested
            properties[field] = container
    hashes = properties.get("dimension_hashes", {})
    if isinstance(hashes, str):
        hashes = json.loads(hashes)
    if digest:
        hashes["why"] = digest
    else:
        hashes.pop("why", None)
    properties["dimension_hashes"] = hashes
    if "why_dimension_hash" in properties:
        properties["why_dimension_hash"] = digest


def _publish_new(path: Path, content: bytes) -> None:
    """An exclusive hard link publishes a complete file without overwriting races."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".risk-why-", delete=False
        ) as handle:
            temp = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp, path)
    except OSError as exc:
        raise AlignmentError("无法写入独立输出文件；目标可能已存在，未覆盖任何快照") from exc
    finally:
        if temp:
            temp.unlink(missing_ok=True)


async def import_why(
    snapshot: Path,
    revision: str,
    output: Path,
    *,
    base_url: str | None = None,
    dimensions_file: Path | None = None,
    node_ids: list[str] | None = None,
    timeout: float = 60,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Validate all requested nodes, then create a separate enriched snapshot.

    ``transport`` allows deterministic, offline transport tests. No retries are
    performed: any remote or validation failure prevents publication entirely.
    """
    snapshot, output = Path(snapshot), Path(output)
    if snapshot.resolve() == output.resolve() or os.path.lexists(output):
        raise AlignmentError("请使用尚不存在的独立输出文件；不能覆盖当前快照或已有文件")
    if bool(base_url) == bool(dimensions_file):
        raise AlignmentError("必须且只能选择本体服务地址或 WHY 离线响应文件")
    if (
        not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or not 0 < timeout <= 120
    ):
        raise AlignmentError("WHY 请求超时必须大于 0 且不超过 120 秒")
    catalog = RiskCatalog.load(snapshot, revision)
    selected = sorted(catalog.names if node_ids is None else node_ids)
    if not selected or len(set(selected)) != len(selected) or set(selected) - catalog.names.keys():
        raise AlignmentError("待补齐节点必须唯一、非空且全部属于固定本体版本")
    imported = {}
    if dimensions_file:
        responses = _read_json(Path(dimensions_file))
        if not isinstance(responses, list):
            raise AlignmentError("WHY 离线文件必须是 concept/dimensions 响应对象数组")
        seen = set()
        for response in responses:
            key = response.get("node_id") if isinstance(response, dict) else None
            if not isinstance(key, str) or key not in catalog.names or key in seen:
                raise AlignmentError("WHY 离线响应存在重复节点或版本外节点")
            seen.add(key)
            verified = _response(response, key, catalog.revision)
            if key in selected:
                imported[key] = verified
        if set(imported) != set(selected):
            raise AlignmentError("WHY 离线文件未覆盖全部选定节点；部分补齐请显式指定 --node-id")
    else:
        root = _base_url(base_url)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(5, timeout)),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        ) as client:
            for key in selected:
                response = await _fetch(client, root, key, catalog.revision, timeout)
                imported[key] = _response(response, key, catalog.revision)

    document = json.loads(catalog.content)
    for key, (_, digest) in imported.items():
        existing = catalog.graph.nodes[key]["why_dimension_hash"]
        if existing and existing != digest:
            raise AlignmentError("相同版本的已有 WHY 与导入原文冲突，未生成输出文件")
    for node in document["graph"]["nodes"]:
        p = node["properties"]
        if (
            node["label"] == "Concept"
            and p.get("dataset_revision") == catalog.revision
            and p["node_id"] in imported
        ):
            _apply(p, *imported[p["node_id"]])
    summary = {
        "dataset_revision": catalog.revision,
        "base_snapshot_sha256": catalog.sha256,
        "source_type": "offline_dimensions" if dimensions_file else "concept_dimensions_api",
        "verified_node_ids": selected,
        "verified_node_count": len(selected),
        "total_node_count": len(catalog.names),
        "unverified_node_count": len(catalog.names) - len(selected),
        "why_node_count": sum(bool(why) for why, _ in imported.values()),
        "empty_why_node_count": sum(not why for why, _ in imported.values()),
        "coverage_complete": len(selected) == len(catalog.names),
        "imported_at": datetime.now(UTC).isoformat(),
    }
    document["risk_why_import"] = summary
    # The original export's embedded graph digest no longer describes its bytes.
    if isinstance(document.get("summary"), dict):
        document["summary"].pop("sha256", None)
    content = (canonical_json(document) + "\n").encode()
    if len(content) > MAX_FILE_BYTES:
        raise AlignmentError("补齐后的本体快照超过 30 MiB，未生成输出文件")
    enriched = RiskCatalog(content, catalog.revision)
    _publish_new(output, content)
    return {**summary, "output": str(output.resolve()), "snapshot_sha256": enriched.sha256}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="为固定版本本体补齐 WHY，校验后写入独立快照文件。")
    parser.add_argument("--snapshot", required=True, type=Path, help="已有本体快照")
    parser.add_argument("--revision", required=True, help="明确选择的 ready 版本")
    parser.add_argument("--output", required=True, type=Path, help="新输出文件（不能存在）")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--base-url", help="本体 API 根地址；缺省使用 BANK_RETRIEVE_BASE_URL")
    sources.add_argument(
        "--dimensions-file", type=Path, help="同版本 concept/dimensions 响应 JSON 数组"
    )
    parser.add_argument(
        "--node-id", action="append", dest="node_ids", help="显式部分补齐，可重复；缺省全部节点"
    )
    parser.add_argument("--timeout", type=float, default=60, help="每节点请求秒数，最多120秒")
    args = parser.parse_args(argv)
    try:
        base_url = args.base_url
        if not args.dimensions_file and not base_url:
            base_url = Settings().retrieve_base_url
        summary = asyncio.run(
            import_why(
                args.snapshot,
                args.revision,
                args.output,
                base_url=base_url,
                dimensions_file=args.dimensions_file,
                node_ids=args.node_ids,
                timeout=args.timeout,
            )
        )
    except (AlignmentError, ValueError, OSError) as exc:
        detail = (
            exc.message if isinstance(exc, AlignmentError) else "配置或文件无效，未生成输出文件"
        )
        parser.exit(1, detail + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
