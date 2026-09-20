"""Export a complete immutable business graph, independently of UI preview limits."""

import json
from uuid import UUID

from bank_project.alignment.display import display_name
from bank_project.alignment.models import AlignmentError


class DatabaseGraphs:
    def __init__(self, graph):
        self.graph = graph

    def sources(self):
        if not self.graph.configured:
            return []
        browser = self.graph.browser
        with browser.session() as session:
            rows = browser.query(
                session,
                "MATCH (v:BankAlignmentVersion {status:'ready'}) RETURN v.id AS id,v.summary AS summary ORDER BY v.id",
            )
        result = []
        for row in rows:
            summary = json.loads(row["summary"])
            result.append(
                {
                    "kind": "database",
                    "id": row["id"],
                    "name": f"DB 图谱 · {summary.get('created_at', row['id'])}",
                    "node_count": summary["node_count"],
                    "edge_count": summary["edge_count"],
                }
            )
        return result

    def load(self, version):
        try:
            UUID(version)
        except ValueError as exc:
            raise AlignmentError("图谱版本标识无效") from exc
        browser = self.graph.browser
        nodes, edges = [], []
        with browser.session() as session:
            summary = browser.version(session, version)
            after = ""
            while True:
                rows = browser.query(
                    session,
                    "MATCH (n:BankAlignedInstance {version:$version}) WHERE n.id>$after RETURN n.id AS id,n.data AS data,properties(n) AS stored ORDER BY n.id LIMIT 500",
                    version=version,
                    after=after,
                )
                if not rows:
                    break
                for row in rows:
                    raw = json.loads(row["data"])
                    nodes.append(
                        {
                            "id": row["id"],
                            "name": display_name(
                                raw.get("fields", {}),
                                raw.get("concept_name") or raw.get("name", "实体"),
                                raw.get("source_row", 0),
                            ),
                            "type": raw.get("concept_name") or "",
                            "properties": raw,
                            "storage_properties": row["stored"],
                            **({"boid": raw["concept_id"]} if raw.get("concept_id") else {}),
                        }
                    )
                after = rows[-1]["id"]
            after = ""
            while True:
                # No endpoint-in-current-page filter: inter-page edges must survive.
                rows = browser.query(
                    session,
                    "MATCH (a:BankAlignedInstance {version:$version})-[r:BANK_SOURCE_LINK {version:$version}]->(b:BankAlignedInstance {version:$version}) WHERE elementId(r)>$after RETURN elementId(r) AS id,a.id AS source,b.id AS target,type(r) AS type,properties(r) AS properties ORDER BY elementId(r) LIMIT 1000",
                    version=version,
                    after=after,
                )
                if not rows:
                    break
                edges.extend(
                    {
                        "id": row["id"],
                        "source": row["source"],
                        "target": row["target"],
                        "properties": row["properties"],
                        "storage_type": row["type"],
                    }
                    for row in rows
                )
                after = rows[-1]["id"]
            if len(nodes) != summary.node_count or len(edges) != summary.edge_count:
                raise AlignmentError("图谱实际数量与发布版本不一致，未采用不完整图谱", 409)
        return {
            "id": version,
            "name": f"DB 图谱 · {summary.created_at}",
            "source_kind": "database",
            "source_id": version,
            "nodes": nodes,
            "edges": edges,
        }
