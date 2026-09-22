"""Read-only, version-pinned graph navigation with bounded payloads and keyset paging."""

from contextlib import contextmanager

from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired

from .display import readable_node
from .models import (
    AlignmentError,
    GraphEdge,
    GraphGroup,
    GraphNode,
    GraphNodeBrief,
    GraphOverview,
    GraphPage,
    GraphSummary,
)


class GraphBrowser:
    def __init__(self, graph):
        self.graph = graph

    @contextmanager
    def session(self):
        try:
            with (
                self.graph._driver() as driver,
                driver.session(database=self.graph.settings.neo4j_database) as session,
            ):
                yield session
        except (Neo4jError, ServiceUnavailable, SessionExpired, OSError) as exc:
            raise AlignmentError("图谱读取未完成，请缩小查询范围或稍后重试", 503) from exc

    def query(self, session, text, **params):
        return self.graph._query(session, text, **params)

    def version(self, session, version):
        rows = self.query(
            session,
            "MATCH (v:BankAlignmentVersion {id:$version,status:'ready'}) RETURN v.summary AS summary",
            version=version,
        )
        if not rows:
            raise AlignmentError("图谱版本不存在或尚未发布，请刷新图谱", 404)
        return GraphSummary.model_validate_json(rows[0]["summary"])

    def overview(self, version: str | None = None):
        if not self.graph.configured:
            if version is not None:
                raise AlignmentError("业务图谱未配置，无法读取指定版本", 503)
            return GraphOverview()
        with self.session() as session:
            if version is not None:
                summary = self.version(session, version)
            else:
                rows = self.query(
                    session,
                    "MATCH (s:BankAlignmentState {name:'current'}) MATCH (v:BankAlignmentVersion {id:s.version,status:'ready'}) RETURN v.summary AS summary",
                )
                if not rows:
                    return GraphOverview()
                summary = GraphSummary.model_validate_json(rows[0]["summary"])
            groups = self.query(
                session,
                """MATCH (n:BankAlignedInstance {version:$version})
                RETURN n.concept_id AS concept_id, min(n.concept_name) AS concept_name,
                       count(n) AS count ORDER BY count DESC, concept_id""",
                version=summary.version,
            )
            return GraphOverview(summary=summary, groups=[GraphGroup(**r) for r in groups])

    def _node(self, session, version, node_id):
        rows = self.query(
            session,
            "MATCH (n:BankAlignedInstance {version:$version,id:$id}) RETURN n.data AS data",
            version=version,
            id=node_id,
        )
        if not rows:
            raise AlignmentError("该实例不在此图谱版本中", 404)
        return readable_node(GraphNode.model_validate_json(rows[0]["data"]))

    def detail(self, version, node_id):
        with self.session() as session:
            self.version(session, version)
            return self._node(session, version, node_id)

    def page(self, version, *, concept="", query="", after="", limit=100, focus=""):
        if not 1 <= limit <= 200:
            raise AlignmentError("每批仅支持 1–200 个实例")
        with self.session() as session:
            self.version(session, version)
            anchor = self._node(session, version, focus) if focus else None
            clauses = ["n.version=$version"]
            if concept:
                clauses.append("n.concept_id=$concept")
            if query.strip():
                # Search source values on the server, never just the current browser page.
                # Parameters are literal substring values, not regex or Cypher fragments.
                clauses.append("""(toLower(n.display_name) CONTAINS $term OR
                    toLower(n.id) CONTAINS $term OR any(k IN keys(n) WHERE
                    k STARTS WITH 'field_' AND toLower(toString(n[k])) CONTAINS $term))""")
            if focus:
                clauses.append("""EXISTS { MATCH (a:BankAlignedInstance {version:$version,id:$focus})
                    -[:BANK_SOURCE_LINK {version:$version}]-(n) }""")
            where = " AND ".join(clauses)
            params = dict(version=version, concept=concept, term=query.strip().lower(), focus=focus)
            total = self.query(
                session,
                f"MATCH (n:BankAlignedInstance) WHERE {where} RETURN count(n) AS count",
                **params,
            )[0]["count"]
            rows = self.query(
                session,
                f"MATCH (n:BankAlignedInstance) WHERE {where} AND n.id>$after RETURN n.id AS id ORDER BY id LIMIT $limit",
                **params,
                after=after,
                limit=limit + 1,
            )
            ids = [r["id"] for r in rows[:limit]]
            nodes = {}
            # Full fields are only read in small bounded batches to derive old display labels.
            # They are not returned in the page payload; detail is loaded for one clicked node.
            for start in range(0, len(ids), 8):
                data = self.query(
                    session,
                    "MATCH (n:BankAlignedInstance {version:$version}) WHERE n.id IN $ids RETURN n.data AS data",
                    version=version,
                    ids=ids[start : start + 8],
                )
                for record in data:
                    node = readable_node(GraphNode.model_validate_json(record["data"]))
                    nodes[node.id] = GraphNodeBrief.model_validate(node.model_dump())
            edge_ids = list(dict.fromkeys([*ids, *([focus] if focus else [])]))
            edges = (
                self.query(
                    session,
                    """MATCH (a:BankAlignedInstance {version:$version})-[r:BANK_SOURCE_LINK {version:$version}]->(b:BankAlignedInstance {version:$version})
                WHERE a.id IN $ids AND b.id IN $ids
                RETURN a.id AS source,b.id AS target,r.relation_id AS relation_id,
                       r.origin AS origin,coalesce(r.name,'') AS name
                ORDER BY source,target,relation_id LIMIT 501""",
                    version=version,
                    ids=edge_ids,
                )
                if ids
                else []
            )
            return GraphPage(
                nodes=[nodes[id] for id in ids],
                anchor=GraphNodeBrief.model_validate(anchor.model_dump()) if anchor else None,
                total=total,
                next_cursor=ids[-1] if len(rows) > limit else None,
                edges=[GraphEdge(**e) for e in edges[:500]],
                edges_truncated=len(edges) > 500,
            )
