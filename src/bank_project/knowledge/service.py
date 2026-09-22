"""Reuse the existing retrieve decision pipeline without rewriting source facts."""

import asyncio
import copy
from datetime import UTC, datetime
from uuid import uuid4

from bank_project.alignment.matching import decide, reviewable_candidate
from bank_project.alignment.models import AlignmentError, RetrievalTrace
from bank_project.alignment.retrieval import RetrievalFailure, RetrieveClient
from bank_project.conversion.audit import review_record
from bank_project.conversion.review import require_reviewable, review_catalog
from bank_project.conversion.versions import GraphVersion, require_revision
from bank_project.graphrag.runtime import GraphRagError
from bank_project.ontology.service import OntologyService

from .database import DatabaseGraphs
from .store import MatchStore


def now():
    return datetime.now(UTC).isoformat()


class KnowledgeService:
    def __init__(self, settings, graphrag, database_graph, *, ontology=None):
        self.settings, self.graphrag = settings, graphrag
        self.ontology = ontology or OntologyService(settings)
        self.database = DatabaseGraphs(database_graph)
        self.store = MatchStore(settings.data_dir / "knowledge" / "matches.sqlite3")
        self.retriever = RetrieveClient(
            settings.retrieve_base_url,
            settings.retrieve_timeout_seconds,
            settings.retrieve_concurrency,
        )
        self.tasks = set()
        self.resolution = None

    def sources(self):
        sources = [
            {
                "kind": "graphrag",
                "id": item["key"],
                "name": item["name"],
                "root_source_id": item["key"],
            }
            for item in self.graphrag.list_datasets()
            if item["status"] == "succeeded"
        ]
        for item in self.graphrag.list_datasets():
            if item["status"] == "succeeded" and item.get("artifacts", {}).get(
                "resolution_records"
            ):
                sources.append(
                    {
                        "kind": "graphrag",
                        "id": "extracted:" + item["key"],
                        "name": item["name"] + " · 聚合前记录",
                        "root_source_id": item["key"],
                    }
                )
        # A missing optional Neo4j must not prevent access to TXT graphs.
        try:
            sources.extend(self.database.sources())
        except AlignmentError as exc:
            sources.append(
                {"kind": "database", "id": "", "name": "DB 图谱暂不可用", "error": exc.message}
            )
        for run in self.store.list():
            if run["status"] == "ready":
                sources.append(
                    {
                        "kind": run["source_kind"],
                        "id": GraphVersion("match", run["id"], run["revision"]).reference,
                        "name": f"{run['name']} · 匹配修订 {run['revision']}",
                        "root_source_id": run.get("root_source_id", run["source_id"]),
                    }
                )
        if self.resolution:
            for run in self.resolution.list():
                if run.status == "ready":
                    sources.append(
                        {
                            "kind": run.source_kind,
                            "id": GraphVersion("resolution", run.id, run.revision).reference,
                            "name": f"{run.name} · 消歧修订 {run.revision}",
                            "root_source_id": run.diagnostics.get("root_source_id", run.source_id),
                        }
                    )
        return sources

    def load_graph(self, kind, identifier):
        if kind not in {"graphrag", "database"}:
            raise AlignmentError("不支持的图谱来源")
        try:
            if identifier.startswith(("match:", "resolution:")):
                graph = self.derived_graph(kind, identifier)
            elif kind == "graphrag":
                graph = (
                    self.graphrag.raw_graph(identifier.removeprefix("extracted:"))
                    if identifier.startswith("extracted:")
                    else self.graphrag.graph(identifier)
                )
            else:
                graph = self.database.load(identifier)
            return self.validate_graph(graph)
        except (AlignmentError, GraphRagError):
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AlignmentError("图谱文件或数据格式不完整，请修复来源后重试", 503) from exc

    def derived_graph(self, kind, identifier):
        version = GraphVersion.parse(identifier)
        if version.stage == "match":
            graph = self.result_graph(version.run_id, expected_revision=version.revision)
        elif version.stage == "resolution" and self.resolution:
            graph = self.resolution.graph(version.run_id)
            require_revision(
                graph["resolution"]["revision"],
                version.revision,
                "消歧版本已更新，请刷新来源并重新选择",
            )
        else:
            raise AlignmentError("派生图来源不可用", 404)
        if graph["source_kind"] != kind:
            raise AlignmentError("派生图与所选来源类型不一致")
        graph["root_source_id"] = graph.get("root_source_id", graph["source_id"])
        graph["parent_graph"] = {
            "id": graph["id"],
            "source_kind": kind,
            "source_id": graph["source_id"],
        }
        graph.update(id=identifier, source_id=identifier)
        return graph

    @staticmethod
    def validate_graph(graph):
        ids = [node["id"] for node in graph["nodes"]]
        edge_ids = [edge["id"] for edge in graph["edges"]]
        if len(ids) != len(set(ids)) or len(edge_ids) != len(set(edge_ids)):
            raise AlignmentError("图谱包含重复的节点或边标识，无法安全处理")
        known = set(ids)
        if any(e["source"] not in known or e["target"] not in known for e in graph["edges"]):
            raise AlignmentError("图谱包含缺失端点的边，无法安全处理")
        return graph

    async def start_match(self, kind, identifier):
        if kind != "graphrag":
            raise AlignmentError("追加 BOID 的匹配适用于 GraphRAG 图谱")
        catalog = await asyncio.to_thread(self.ontology.current)
        try:
            await self.retriever.check_revision(catalog.revision)
        except RetrievalFailure as exc:
            raise AlignmentError(exc.detail, 503) from exc
        graph = await asyncio.to_thread(self.load_graph, kind, identifier)
        value = {
            "id": str(uuid4()),
            "name": graph["name"],
            "source_kind": kind,
            "source_id": identifier,
            "root_source_id": graph.get("root_source_id", identifier),
            "status": "running",
            "progress": "正在匹配节点",
            "error": None,
            "revision": 0,
            "created_at": now(),
            "ontology_revision": catalog.revision,
            "ontology_id": catalog.ontology_id,
            "snapshot_sha256": catalog.sha256,
            "confidence_threshold": self.settings.alignment_min_confidence,
            "summary": {
                "node_count": len(graph["nodes"]),
                "edge_count": len(graph["edges"]),
                "matched_nodes": 0,
                "matched_edges": 0,
            },
            "nodes": [],
            "edges": [],
            "audits": [],
            "graph": graph,
        }
        await asyncio.to_thread(self.store.create, value)
        task = asyncio.create_task(self._match(value, catalog))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return self.store.public(value)

    async def _match(self, value, catalog):
        identifier, graph = value["id"], value["graph"]
        try:
            nodes = []
            async with self.retriever.session(catalog.revision) as session:
                for start in range(0, len(graph["nodes"]), 100):
                    batch = graph["nodes"][start : start + 100]
                    queries = [self.node_query(n) for n in batch]
                    results = await session.search_many(queries)
                    for node, result in zip(batch, results, strict=True):
                        trace = decide(
                            result,
                            "entity",
                            node["name"],
                            catalog,
                            self.settings.alignment_min_confidence,
                        )
                        # A derived graph is a versioned input: retain its accepted
                        # annotations while exposing fresh retrieval evidence.
                        inherited = node.get("boid")
                        candidate = reviewable_candidate(trace, catalog)
                        nodes.append(
                            {
                                "id": node["id"],
                                "name": node["name"],
                                "boid": inherited
                                or (
                                    candidate.id
                                    if candidate and trace.status == "matched"
                                    else None
                                ),
                                "trace": trace.model_dump(),
                            }
                        )

                    def checkpoint(current):
                        current.update(
                            nodes=nodes,
                            progress=f"已匹配 {len(nodes)}/{len(graph['nodes'])} 个节点",
                        )
                        self.summarize(current)

                    await asyncio.to_thread(self.store.mutate, identifier, checkpoint)
            value["nodes"] = nodes
            value["edges"] = await asyncio.to_thread(self.edge_matches, value, catalog)
            self.summarize(value)
            value.update(
                status="ready",
                progress="匹配方案已就绪，可整体采纳建议或按需修改；原图属性保留",
                revision=1,
            )
            await asyncio.to_thread(self.store.mutate, identifier, lambda v: v.update(value))
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self.store.mutate,
                identifier,
                lambda v: v.update(status="failed", error="匹配任务中断，请重新发起"),
            )
            raise
        except Exception as exc:
            message = (
                exc.message
                if isinstance(exc, AlignmentError)
                else "匹配未完成，请检查本体检索配置后重试"
            )
            await asyncio.to_thread(
                self.store.mutate,
                identifier,
                lambda v: v.update(status="failed", error=message, progress="匹配失败"),
            )

    @staticmethod
    def node_query(node):
        properties = node.get("properties", {})
        # GraphRAG already extracted the semantic type and description. The same
        # retrieve transport, revision gate and confidence decision are reused.
        parts = [
            str(node.get("type", "")),
            str(node["name"]),
            str(properties.get("description", "")),
        ]
        return " ".join(p for p in parts if p).strip()[:4000] or node["id"]

    @staticmethod
    def edge_matches(value, catalog):
        concepts = {node["id"]: node["boid"] for node in value["nodes"]}
        relations = {}
        for relation in catalog.relations:
            relations.setdefault((relation["source"], relation["target"]), set()).add(
                relation["type"]
            )
        results = []
        for edge in value["graph"]["edges"]:
            candidates = sorted(
                relations.get((concepts.get(edge["source"]), concepts.get(edge["target"])), set())
            )
            raw = edge.get("properties", {})
            declared = {
                str(raw.get(key, "")).strip()
                for key in ("type", "name", "relation_type", "edge_type")
            }
            exact = [candidate for candidate in candidates if candidate in declared]
            exact_match = exact[0] if len(exact) == 1 else None
            selected = edge.get("edge_type") or exact_match
            proposed = exact_match or (candidates[0] if len(candidates) == 1 else None)
            detail = (
                "原边类型与本体方向及两端 BOID 一致"
                if exact_match
                else "本体两端及方向只有一个候选关系，可核对后采纳；原始关系描述保留"
                if proposed
                else "请结合原边证据人工选择类型；原始关系描述保留"
            )
            if not candidates:
                detail = "两端尚未匹配或本体无对应方向的关系，保留原边待核对"
            if edge.get("edge_type"):
                detail = "保留所选图谱版本已挂载的边类型；本次候选供核对"
            results.append(
                {
                    "id": edge["id"],
                    "source": edge["source"],
                    "target": edge["target"],
                    "edge_type": selected,
                    "proposed_edge_type": proposed,
                    "candidates": candidates,
                    "detail": detail,
                    "status": "matched" if selected else "review" if candidates else "unmatched",
                }
            )
        return results

    def refresh_edges(self, value, catalog, node_ids=None):
        """Refresh dependent suggestions without overwriting a person's decision."""
        refreshed = {edge["id"]: edge for edge in self.edge_matches(value, catalog)}
        for edge in value["edges"]:
            if node_ids is not None and not node_ids.intersection((edge["source"], edge["target"])):
                continue
            fresh = refreshed[edge["id"]]
            edge.update(
                candidates=fresh["candidates"],
                proposed_edge_type=fresh["proposed_edge_type"],
            )
            if edge["edge_type"] not in edge["candidates"]:
                edge.update(
                    edge_type=None,
                    status="review" if edge["candidates"] else "unmatched",
                    detail="端点 BOID 已修改，请重新审核边类型",
                )

    @staticmethod
    def summarize(value):
        value["summary"]["matched_nodes"] = sum(bool(n["boid"]) for n in value["nodes"])
        value["summary"]["matched_edges"] = sum(bool(e["edge_type"]) for e in value["edges"])

    def editable(self, value):
        require_reviewable(value["status"])
        return review_catalog(
            self.ontology,
            value["ontology_revision"],
            value["snapshot_sha256"],
            value.get("ontology_id"),
        )

    def concepts(self, identifier, query):
        return self.editable(self.store.get(identifier)).search(query)

    @staticmethod
    def reviewed_items(value):
        # Legacy manual decisions may predate the explicit reviewed flag.
        return {
            (audit["target"], audit["target_id"])
            for audit in value.get("audits", [])
            if audit.get("action") != "refresh_endpoints"
        }

    @staticmethod
    def audit(value, target, before, after, payload, action="review"):
        value["audits"].append(
            review_record(
                action=action,
                target=target,
                target_id=after["id"],
                before=before,
                after=after,
                reviewer=payload.reviewer,
                note=payload.note,
                revision=value["revision"],
            )
        )

    def accept_proposals(self, identifier, payload):
        def change(value):
            catalog = self.editable(value)
            reviewed = self.reviewed_items(value)
            changes, changed_nodes = [], set()
            for node in value["nodes"]:
                if node.get("reviewed") or ("node", node["id"]) in reviewed or node["boid"]:
                    continue
                candidate = reviewable_candidate(
                    RetrievalTrace.model_validate(node["trace"]), catalog
                )
                if candidate is None:
                    continue
                before = copy.deepcopy(node)
                node.update(boid=candidate.id, reviewed=True)
                changes.append(("node", before, node, "accept_proposal"))
                changed_nodes.add(node["id"])
            # Compute the edge plan after accepting nodes. A relation remains a
            # proposal until this explicit action; ontology presence is not a fact.
            edge_before = {edge["id"]: copy.deepcopy(edge) for edge in value["edges"]}
            self.refresh_edges(value, catalog, changed_nodes)
            refreshed = {edge["id"]: edge for edge in self.edge_matches(value, catalog)}
            for edge in value["edges"]:
                fresh = refreshed[edge["id"]]
                edge.update(
                    candidates=fresh["candidates"],
                    proposed_edge_type=fresh["proposed_edge_type"],
                )
                if (
                    not edge["edge_type"]
                    and not edge.get("reviewed")
                    and ("edge", edge["id"]) not in reviewed
                    and fresh["proposed_edge_type"]
                ):
                    edge.update(
                        edge_type=fresh["proposed_edge_type"],
                        reviewed=True,
                        status="matched",
                        detail="已整体采纳本体关系建议；原始关系描述保留",
                    )
                    changes.append(("edge", edge_before[edge["id"]], edge, "accept_proposal"))
                elif edge_before[edge["id"]]["edge_type"] != edge["edge_type"]:
                    changes.append(("edge", edge_before[edge["id"]], edge, "refresh_endpoints"))
            if changes:
                value["revision"] += 1
                for target, before, after, action in changes:
                    self.audit(value, target, before, after, payload, action)
                value["progress"] = "已整体采纳匹配建议；可继续查看匹配过程或按需修改"
            self.summarize(value)

        return self.store.public(self.store.mutate(identifier, change, payload.expected_revision))

    def review(self, identifier, payload):
        def change(value):
            catalog = self.editable(value)
            target, key = payload.target, payload.target_id
            collection = value["nodes"] if target == "node" else value["edges"]
            item = next((item for item in collection if item["id"] == key), None)
            if item is None:
                raise AlignmentError("匹配节点或边不存在", 404)
            before = copy.deepcopy(item)
            if target == "node":
                if payload.boid is not None and payload.boid not in catalog.names:
                    raise AlignmentError("BOID 不在本次本体版本中")
                item["boid"] = payload.boid
                # Keep the original retrieve trace and store review separately.
                item["reviewed"] = True
                self.refresh_edges(value, catalog, {key})
            else:
                if payload.edge_type is not None and payload.edge_type not in item["candidates"]:
                    raise AlignmentError("边类型必须符合本体中两端 BOID 的方向约束")
                item.update(
                    edge_type=payload.edge_type,
                    reviewed=True,
                    status="matched" if payload.edge_type else "review",
                )
            value["revision"] += 1
            self.audit(value, target, before, item, payload)
            self.summarize(value)

        return self.store.public(self.store.mutate(identifier, change, payload.expected_revision))

    def result_graph(self, identifier, expected_revision=None):
        value = self.store.get(identifier)
        if value["status"] != "ready":
            raise AlignmentError("匹配结果尚未完成", 409)
        require_revision(
            value["revision"], expected_revision, "匹配版本已更新，请刷新来源并重新选择"
        )
        graph = copy.deepcopy(value["graph"])
        graph["parent_graph"] = {
            "id": graph["id"],
            "source_kind": graph["source_kind"],
            "source_id": graph["source_id"],
        }
        graph["id"] = identifier
        graph["name"] = f"{value['name']} · 匹配修订 {value['revision']}"
        nodes = {n["id"]: n for n in value["nodes"]}
        edges = {e["id"]: e for e in value["edges"]}
        for node in graph["nodes"]:
            if nodes[node["id"]]["boid"] is not None:
                node["boid"] = nodes[node["id"]]["boid"]
            else:
                node.pop("boid", None)
        for edge in graph["edges"]:
            if edges[edge["id"]]["edge_type"] is not None:
                edge["edge_type"] = edges[edge["id"]]["edge_type"]
            else:
                edge.pop("edge_type", None)
        graph["matching"] = {
            "run_id": identifier,
            "revision": value["revision"],
            "ontology_revision": value["ontology_revision"],
            "ontology_id": value.get("ontology_id"),
            "snapshot_sha256": value["snapshot_sha256"],
        }
        return graph

    async def close(self):
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
