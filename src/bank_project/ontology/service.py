"""One authority for table matching, TXT matching, browsing and risk evidence."""

import asyncio
import json
import threading

import httpx

from bank_project.alignment.catalog import Catalog
from bank_project.alignment.models import AlignmentError

from .client import MAX_RESPONSE_BYTES, OntologyClient
from .dimensions import read_dimension
from .export import encode_catalog, remote_catalog
from .serialization import canonical_json, dimension_hash
from .store import SnapshotStore


class OntologyService:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.client = OntologyClient(settings, transport=transport)
        self.snapshots = SnapshotStore(settings.data_dir / "ontology" / "snapshots")
        self._lock = threading.RLock()
        self._catalog = None
        self._risk_catalog = None
        self._dimensions = {}
        # Archive original bytes before switching sources. Legacy jobs refer to
        # their file digest, which must never be rewritten to a new encoding.
        self.snapshots.import_legacy(settings.ontology_snapshot)

    @property
    def remote(self):
        return bool(self.settings.ontology_base_url)

    def _run(self, operation):
        async def bounded():
            async with asyncio.timeout(self.client.timeout):
                return await operation

        try:
            return asyncio.run(bounded())
        except (httpx.HTTPError, TimeoutError) as exc:
            raise AlignmentError("远端本体读取失败或超时，请检查连接后重试", 503) from exc
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise AlignmentError("远端本体数据不完整或格式无效，未采用本次数据", 503) from exc

    def current(self, revision=None):
        expected = revision or self.settings.ontology_revision
        with self._lock:
            if self.remote:
                catalog = self._run(self._current(expected))
            else:
                catalog = Catalog.load(self.settings.ontology_snapshot, expected)
            self.snapshots.save(catalog)
            return catalog

    async def _current(self, expected):
        async with self.client.session() as client:
            ready = await self.client.ready(client, expected)
            if (
                self._catalog is not None
                and self._catalog.revision == ready["dataset_revision"]
                and self._catalog.ontology_id == ready["ontology_id"]
            ):
                return self._catalog
            export = await self.client.request(
                client, "GET", "/evaluation-snapshot", revision=ready["dataset_revision"]
            )
            catalog = remote_catalog(export, ready)
            self._catalog = catalog
            self._risk_catalog = None
            self._dimensions.clear()
            return catalog

    def pinned(self, revision, digest, ontology_id=None):
        """Historical reviews require exact evidence, never a live remote request."""
        catalog = self.snapshots.load(revision, digest)
        if ontology_id and catalog.ontology_id != ontology_id:
            raise AlignmentError("历史任务与本体依据的 ID 不一致", 409)
        return catalog

    @staticmethod
    def info(catalog):
        return {
            "kind": catalog.source.get("kind", "local"),
            "ontology_id": catalog.ontology_id,
            "revision": catalog.revision,
            "snapshot_sha256": catalog.sha256,
            "node_count": len(catalog.names),
            "relation_count": len(catalog.relations),
            "why_node_count": len(catalog.why_ids),
        }

    async def _read_dimensions(self, client, catalog, node_id, names):
        key = (catalog.sha256, node_id)
        cached = self._dimensions.get(key, {})
        missing = [name for name in names if name not in cached]
        if missing:
            response = await self.client.dimensions(client, catalog, node_id, missing)
            values, hashes = response.get("dimensions"), response.get("dimension_hashes")
            if not isinstance(values, dict) or not isinstance(hashes, dict):
                raise ValueError("invalid dimensions")
            expected = catalog.metadata[node_id].get("dimension_hashes", {})
            verified = {}
            for name in missing:
                if name not in values or (
                    values[name] is not None and not isinstance(values[name], (dict, list))
                ):
                    raise ValueError("invalid dimension content")
                digest = dimension_hash(values[name])
                if digest != hashes.get(name) or digest != expected.get(name):
                    raise AlignmentError("维度原文与本体目录哈希不一致", 409)
                verified[name] = values[name]
            cached = {**cached, **verified}
            self._dimensions[key] = cached
        return {name: cached[name] for name in names}

    def dimensions(self, revision, digest, node_id):
        catalog = self.pinned(revision, digest)
        if node_id not in catalog.names:
            raise AlignmentError("概念不在所选本体版本中", 404)
        with self._lock:
            if catalog.source.get("kind") != "remote":
                return {
                    name: read_dimension(catalog.metadata[node_id], name)
                    for name in ("what", "why")
                }
            if not self.remote:
                raise AlignmentError("请配置历史本体对应的服务地址，才能读取未保存的维度", 503)
            return self._run(self._concept_dimensions(catalog, node_id))

    async def _concept_dimensions(self, catalog, node_id):
        async with self.client.session() as client:
            ready = await self.client.ready(client, catalog.revision)
            if ready["ontology_id"] != catalog.ontology_id:
                raise AlignmentError("当前服务与历史本体 ID 不一致", 409)
            return await self._read_dimensions(client, catalog, node_id, ["what", "why"])

    def for_risk(self, revision=None):
        from bank_project.risk.catalog import RiskCatalog

        with self._lock:
            catalog = self.current(revision)
            if not self.remote:
                return RiskCatalog(catalog.content, catalog.revision)
            if self._risk_catalog is None or self._risk_catalog.revision != catalog.revision:
                self._risk_catalog = self._run(self._with_why(catalog))
                self.snapshots.save(self._risk_catalog)
            return self._risk_catalog

    async def _with_why(self, catalog):
        from bank_project.risk.catalog import RiskCatalog

        document = json.loads(catalog.content)
        selected = [n for n in document["graph"]["nodes"] if n["label"] == "Concept"]
        why_nodes = [n for n in selected if "why" in n["properties"].get("nonempty_dimensions", [])]
        pending = iter(why_nodes)
        total_bytes = 0
        async with self.client.session() as client:

            async def worker():
                nonlocal total_bytes
                for node in pending:
                    props = node["properties"]
                    why = (await self._read_dimensions(client, catalog, props["node_id"], ["why"]))[
                        "why"
                    ]
                    total_bytes += len(canonical_json(why).encode())
                    if total_bytes > MAX_RESPONSE_BYTES:
                        raise AlignmentError("WHY 内容合计超过 30 MiB 上限", 503)
                    props["why"] = why

            tasks = [
                asyncio.create_task(worker()) for _ in range(self.settings.retrieve_concurrency)
            ]
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        document["risk_why_import"] = {
            "source_type": "ontology_api",
            "ontology_id": catalog.ontology_id,
            "dataset_revision": catalog.revision,
            "base_snapshot_sha256": catalog.sha256,
            "total_node_count": len(selected),
            "verified_node_count": len(selected),
            "unverified_node_count": 0,
            "why_node_count": len(why_nodes),
            "empty_why_node_count": len(selected) - len(why_nodes),
            "coverage_complete": True,
        }
        enriched = encode_catalog(document, catalog.revision)
        return RiskCatalog(enriched.content, catalog.revision)
