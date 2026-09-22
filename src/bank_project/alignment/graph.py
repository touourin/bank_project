"""Build isolated Neo4j versions, then publish with an atomic pointer and fencing token."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from neo4j import GraphDatabase, Query
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired

from bank_project.settings import Settings

from .browsing import GraphBrowser
from .models import AlignmentError, GraphEdge, GraphNode, GraphPreview, GraphSummary, Run
from .relations import join_keys


def node_id(table_id: str, position: int) -> str:
    return f"{table_id}:{position}"


def graph_rows(sources, result, staging=None):
    by_id = {s.table.id: s for s in sources}
    for mapping in result.tables:
        if mapping.status != "mapped":
            continue
        source = by_id[mapping.table_id]
        fields = [(i, c) for i, c in enumerate(mapping.columns) if c.role != "ignore"]
        name_column = next(
            (i for i, c in fields if c.semantic.lower() in {"name", "名称", "姓名"}), None
        )
        rows = (
            staging.iter_rows(source.table.id)
            if source.staged and staging
            else enumerate(source.rows)
        )
        if source.staged and staging is None:
            raise AlignmentError("暂存数据服务不可用", 503)
        for position, row in rows:
            name = row.values[name_column] if name_column is not None else None
            node = GraphNode(
                id=node_id(source.table.id, position),
                name=(name or f"{source.table.name} · 第 {row.number} 行")[:200],
                table_id=source.table.id,
                table_name=source.table.name,
                source_row=row.number,
                concept_id=mapping.concept_id,
                concept_name=mapping.concept_name,
                fields={c.column: row.values[i] for i, c in fields},
            )
            yield {
                "id": node.id,
                "data": node.model_dump_json(),
                "properties": {
                    "source_batch_id": source.batch.id,
                    "source_table_id": source.table.id,
                    "source_row": row.number,
                    "concept_id": mapping.concept_id,
                    "concept_name": mapping.concept_name,
                    "display_name": node.name,
                    **{c.property_key: row.values[i] for i, c in fields},
                },
            }


def graph_edges(sources, result, index=None):
    by_id = {s.table.id: s for s in sources}
    for relation in result.relations:
        if relation.status != "ready":
            continue
        source, target = by_id[relation.source_table_id], by_id[relation.target_table_id]
        if source.staged or target.staged:
            if index is None:
                raise AlignmentError("暂存关联索引不可用", 503)
            for i, j in index.pairs(
                source, target, relation.source_columns, relation.target_columns
            ):
                yield GraphEdge(
                    source=node_id(source.table.id, i),
                    target=node_id(target.table.id, j),
                    relation_id=relation.id,
                    origin=relation.origin,
                ).model_dump()
            continue
        lookup = {
            key: i
            for i, key in enumerate(join_keys(target, relation.target_columns))
            if key is not None
        }
        for i, key in enumerate(join_keys(source, relation.source_columns)):
            if key is not None and key in lookup:
                yield GraphEdge(
                    source=node_id(source.table.id, i),
                    target=node_id(target.table.id, lookup[key]),
                    relation_id=relation.id,
                    origin=relation.origin,
                ).model_dump()


def chunks(items, size=200):
    batch, byte_count = [], 0
    for item in items:
        cost = len(json.dumps(item, ensure_ascii=False).encode())
        if batch and (len(batch) >= size or byte_count + cost > 2 * 1024 * 1024):
            yield batch
            batch, byte_count = [], 0
        batch.append(item)
        byte_count += cost
    if batch:
        yield batch


class VersionedGraph:
    def __init__(self, settings: Settings, staging=None, index=None):
        self.settings, self.staging, self.index = settings, staging, index
        self.browser = GraphBrowser(self)

    @property
    def configured(self):
        return bool(self.settings.neo4j_password)

    def _driver(self):
        s = self.settings
        if not self.configured:
            raise AlignmentError("请先配置业务 Neo4j 的连接信息", 503)
        return GraphDatabase.driver(
            s.neo4j_uri,
            auth=(s.neo4j_user, s.neo4j_password.get_secret_value()),
            connection_timeout=10,
            connection_acquisition_timeout=15,
            max_transaction_retry_time=10,
        )

    @staticmethod
    def _query(session, text, **params):
        return session.run(Query(text, timeout=30), **params).data()

    def publish(self, run: Run, sources, sequence: int, check_lease) -> GraphSummary:
        version = str(uuid4())
        result = run.result
        summary = GraphSummary(
            version=version,
            run_id=run.id,
            revision=result.revision,
            ontology_id=result.ontology_id,
            node_count=0,
            edge_count=0,
            created_at=datetime.now(UTC).isoformat(),
        )
        compiler, compiled = None, None
        if result.template:
            if self.staging is None or self.index is None:
                raise AlignmentError("图模板需要 MySQL 暂存服务", 503)
            from bank_project.staging.compile import TemplateCompiler

            compiler = TemplateCompiler(self.staging, self.index)
            compiled = compiler.compile(sources, result.template, check_lease)

        def node_rows():
            if compiler:
                for item in compiler.rows(compiled, "nodes"):
                    yield {
                        "id": item["id"],
                        "data": json.dumps(item, ensure_ascii=False),
                        "properties": {
                            "source_table_id": item["table_id"],
                            "source_row": item["source_row"],
                            "concept_id": item["concept_id"],
                            "concept_name": item["concept_name"],
                            "display_name": item["name"],
                            **{f"field_{i:03d}": v for i, v in enumerate(item["fields"].values())},
                        },
                    }
            else:
                yield from graph_rows(sources, result, self.staging)

        def edge_rows():
            if compiler:
                yield from compiler.rows(compiled, "edges")
            else:
                yield from graph_edges(sources, result, self.index)

        try:
            with (
                self._driver() as driver,
                driver.session(database=self.settings.neo4j_database) as session,
            ):
                self._query(
                    session,
                    "CREATE CONSTRAINT bank_alignment_state IF NOT EXISTS FOR (s:BankAlignmentState) REQUIRE s.name IS UNIQUE",
                )
                self._query(
                    session,
                    "CREATE CONSTRAINT bank_alignment_version IF NOT EXISTS FOR (v:BankAlignmentVersion) REQUIRE v.id IS UNIQUE",
                )
                self._query(
                    session,
                    "CREATE CONSTRAINT bank_alignment_entity IF NOT EXISTS FOR (n:BankAlignedInstance) REQUIRE (n.version, n.id) IS UNIQUE",
                )
                check_lease()
                # Taking the state lock also fences any previous worker whose lease has expired.
                accepted = self._query(
                    session,
                    "MERGE (s:BankAlignmentState {name:'current'}) SET s.lock=coalesce(s.lock,0)+1 WITH s WHERE coalesce(s.ticket,0)<$ticket SET s.ticket=$ticket RETURN s.ticket AS ticket",
                    ticket=sequence,
                )
                if not accepted:
                    raise AlignmentError("已有更新的图谱生成任务，该任务不会覆盖当前版本", 409)
                self._query(
                    session,
                    "CREATE (v:BankAlignmentVersion {id:$id,status:'building',run_id:$run})",
                    id=version,
                    run=run.id,
                )
                for batch in chunks(node_rows()):
                    check_lease()
                    self._query(
                        session,
                        "UNWIND $rows AS r CREATE (n:BankAlignedInstance {version:$version,id:r.id,data:r.data}) SET n += r.properties",
                        rows=batch,
                        version=version,
                    )
                    summary.node_count += len(batch)
                for batch in chunks(edge_rows()):
                    check_lease()
                    self._query(
                        session,
                        "UNWIND $rows AS r MATCH (a:BankAlignedInstance {version:$version,id:r.source}), (b:BankAlignedInstance {version:$version,id:r.target}) CREATE (a)-[:BANK_SOURCE_LINK {version:$version,relation_id:r.relation_id,origin:r.origin,name:r.name}]->(b)",
                        rows=batch,
                        version=version,
                    )
                    summary.edge_count += len(batch)
                check_lease()
                # Counts, ownership and active pointer are checked/published in one transaction.
                published = self._query(
                    session,
                    """
                    MATCH (s:BankAlignmentState {name:'current'})
                    SET s.lock=coalesce(s.lock,0)+1
                    WITH s WHERE s.ticket=$ticket
                    MATCH (v:BankAlignmentVersion {id:$version,status:'building'})
                    CALL { MATCH (n:BankAlignedInstance {version:$version}) RETURN count(n) AS nc }
                    CALL { MATCH ()-[r:BANK_SOURCE_LINK {version:$version}]->() RETURN count(r) AS ec }
                    WITH s,v,nc,ec WHERE nc=$nodes AND ec=$edges
                    SET v.status='ready', v.summary=$summary, s.version=$version
                    RETURN v.id AS id
                """,
                    ticket=sequence,
                    version=version,
                    nodes=summary.node_count,
                    edges=summary.edge_count,
                    summary=summary.model_dump_json(),
                )
                if not published:
                    raise AlignmentError("图谱写入数量不完整或已有更新任务，未切换当前版本", 409)
                return summary
        except (Neo4jError, ServiceUnavailable, SessionExpired, OSError) as exc:
            # Staged versions remain invisible. Never purge a previously published graph here.
            raise AlignmentError(
                "图谱写入未完成，请检查 Neo4j 后重试；已发布版本保持不变", 503
            ) from exc
        finally:
            if compiler and compiled:
                compiler.release(compiled)

    def current(self) -> GraphPreview:
        if not self.configured:
            return GraphPreview()
        try:
            with (
                self._driver() as driver,
                driver.session(database=self.settings.neo4j_database) as session,
            ):
                records = self._query(
                    session,
                    "MATCH (s:BankAlignmentState {name:'current'}) MATCH (v:BankAlignmentVersion {id:s.version,status:'ready'}) RETURN v.summary AS summary",
                )
                if not records:
                    return GraphPreview()
                summary = GraphSummary.model_validate_json(records[0]["summary"])
                nodes = [
                    GraphNode.model_validate_json(r["data"])
                    for r in self._query(
                        session,
                        "MATCH (n:BankAlignedInstance {version:$version}) RETURN n.data AS data ORDER BY n.id LIMIT 50",
                        version=summary.version,
                    )
                ]
                edges = [
                    GraphEdge(**r)
                    for r in self._query(
                        session,
                        "MATCH (a:BankAlignedInstance {version:$version})-[r:BANK_SOURCE_LINK]->(b:BankAlignedInstance {version:$version}) WHERE a.id IN $ids AND b.id IN $ids RETURN a.id AS source,b.id AS target,r.relation_id AS relation_id,r.origin AS origin,coalesce(r.name,'') AS name LIMIT 100",
                        version=summary.version,
                        ids=[n.id for n in nodes],
                    )
                ]
                return GraphPreview(summary=summary, nodes=nodes, edges=edges)
        except (Neo4jError, ServiceUnavailable, SessionExpired, OSError) as exc:
            raise AlignmentError("暂时无法读取已发布图谱，请检查 Neo4j 连接", 503) from exc
