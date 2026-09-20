"""Compile confirmed templates on disk before publishing a graph version."""

import hashlib
import json
import logging
from uuid import uuid4

from bank_project.alignment.models import AlignmentError, GraphNode

from .keys import encoded_key
from .store import dumps


class TemplateCompiler:
    def __init__(self, store, index):
        self.store, self.index, self.database = store, index, store.database
        with self.database.connect() as db:
            for statement in (
                """CREATE TABLE IF NOT EXISTS graph_draft_runs (
                    version CHAR(36) CHARACTER SET ascii PRIMARY KEY,
                    touched DOUBLE NOT NULL, state VARCHAR(16) NOT NULL) ENGINE=InnoDB""",
                """CREATE TABLE IF NOT EXISTS graph_draft_nodes (
                    version CHAR(36) CHARACTER SET ascii NOT NULL,
                    id CHAR(64) CHARACTER SET ascii NOT NULL, identity_value MEDIUMBLOB NOT NULL,
                    data JSON NOT NULL, PRIMARY KEY(version,id)) ENGINE=InnoDB""",
                """CREATE TABLE IF NOT EXISTS graph_draft_bindings (
                    version CHAR(36) CHARACTER SET ascii NOT NULL,
                    binding VARCHAR(100) CHARACTER SET ascii NOT NULL, position BIGINT NOT NULL,
                    entity_id CHAR(64) CHARACTER SET ascii NOT NULL,
                    PRIMARY KEY(version,binding,position)) ENGINE=InnoDB""",
                """CREATE TABLE IF NOT EXISTS graph_draft_edges (
                    version CHAR(36) CHARACTER SET ascii NOT NULL, id CHAR(64) CHARACTER SET ascii NOT NULL,
                    data JSON NOT NULL, PRIMARY KEY(version,id)) ENGINE=InnoDB""",
            ):
                db.execute(statement)

    def compile(self, sources, template, check):
        version = str(uuid4())
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO graph_draft_runs VALUES(%s,UNIX_TIMESTAMP(),'building')", (version,)
            )
        try:
            return self._compile(sources, template, check, version)
        except BaseException:
            self.release(version)
            raise

    def release(self, version):
        # Cleanup must never hide the result of an already committed graph publication.
        try:
            with self.database.connect() as db:
                db.execute(
                    "UPDATE graph_draft_runs SET state='finished' WHERE version=%s", (version,)
                )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Deferred graph draft cleanup (%s)", type(exc).__name__
            )

    def collect(self):
        with self.database.connect() as db:
            db.execute(
                "SELECT version FROM graph_draft_runs WHERE state='finished' OR touched<UNIX_TIMESTAMP()-3600 LIMIT 1"
            )
            row = db.fetchone()
            if not row:
                return False
            for table in ("graph_draft_bindings", "graph_draft_nodes", "graph_draft_edges"):
                db.execute(f"DELETE FROM {table} WHERE version=%s LIMIT 1000", (row[0],))
                if db.rowcount:
                    return True
            db.execute("DELETE FROM graph_draft_runs WHERE version=%s", (row[0],))
            return True

    def _compile(self, sources, template, check, version):
        if not template.confirmed:
            raise AlignmentError("图模板尚未确认")
        by_id = {s.table.id: s for s in sources}
        nodes = {n.id: n for n in template.nodes}
        for node in template.nodes:
            source = by_id[node.table_id]
            positions = {c.name: i for i, c in enumerate(source.table.columns)}
            pending, bindings, size = {}, [], 0

            def flush(concept_name=node.concept_name):
                nonlocal pending, bindings, size
                if not bindings:
                    return
                check()
                ids = list(pending)
                with self.database.connect() as db:
                    db.execute(
                        "UPDATE graph_draft_runs SET touched=UNIX_TIMESTAMP() WHERE version=%s",
                        (version,),
                    )
                    placeholders = ",".join(["%s"] * len(ids))
                    db.execute(
                        f"SELECT id,identity_value,data FROM graph_draft_nodes WHERE version=%s AND id IN ({placeholders})",
                        (version, *ids),
                    )
                    for key, identity, data in db.fetchall():
                        item = pending[key]
                        if bytes(identity) != item[0]:
                            raise AlignmentError("身份摘要冲突，未发布图谱")
                        old = json.loads(data)
                        merge_fields(old["fields"], item[1]["fields"], concept_name)
                        pending[key] = (identity, old)
                    if any(len(dumps(v[1]).encode()) > 2 * 1024 * 1024 for v in pending.values()):
                        raise AlignmentError("合并后的实例属性超过 2 MB，请缩小模板属性范围")
                    db.executemany(
                        "INSERT INTO graph_draft_nodes VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE data=VALUES(data)",
                        [
                            (version, key, value[0], dumps(value[1]))
                            for key, value in pending.items()
                        ],
                    )
                    db.executemany("INSERT INTO graph_draft_bindings VALUES(%s,%s,%s,%s)", bindings)
                pending, bindings, size = {}, [], 0

            for position, row in self.store.iter_rows(source.table.id):
                values = (
                    [row.values[positions[c]] for c in node.key_columns]
                    if node.key_columns
                    else [source.table.id, str(position)]
                )
                key = encoded_key([node.identity_scope, node.concept_id, *values])
                if key is None:
                    raise AlignmentError(
                        f"节点「{node.concept_name}」在 {source.table.name} 第 {row.number} 行缺少身份标识，请修正模板或数据"
                    )
                key_id = hashlib.sha256(key).hexdigest()
                fields = {
                    p.name: row.values[positions[p.column]]
                    for p in node.properties
                    if row.values[positions[p.column]] is not None
                }
                name = next(
                    (
                        v
                        for k, v in fields.items()
                        if any(term in k.lower() for term in ("姓名", "名称", "name")) and v
                    ),
                    None,
                )
                item = GraphNode(
                    id=key_id,
                    name=(name or str(values[0]))[:200],
                    table_id=source.table.id,
                    table_name=source.table.name,
                    source_row=row.number,
                    concept_id=node.concept_id,
                    concept_name=node.concept_name,
                    fields=fields,
                ).model_dump()
                cost = len(dumps(item).encode()) + len(key)
                if bindings and (len(bindings) >= 200 or size + cost > 1024 * 1024):
                    flush()
                if key_id in pending:
                    if pending[key_id][0] != key:
                        raise AlignmentError("身份摘要冲突")
                    merge_fields(pending[key_id][1]["fields"], fields, node.concept_name)
                else:
                    pending[key_id] = (key, item)
                bindings.append((version, node.id, position, key_id))
                size += cost
            flush()
        for edge in template.edges:
            a, b = nodes[edge.source], nodes[edge.target]
            left, right = by_id[a.table_id], by_id[b.table_id]
            if edge.mode == "join":
                duplicate, _, missing = self.index.stats(
                    left, right, edge.source_columns, edge.target_columns
                )
                if duplicate:
                    raise AlignmentError(f"关系「{edge.name}」目标连接字段不唯一，请修正模板")
                if missing:
                    raise AlignmentError(
                        f"关系「{edge.name}」存在 {missing} 条非空连接值找不到目标，请修正数据或模板"
                    )
                source_set = self.index.ensure(left, edge.source_columns)
                target_set = self.index.ensure(right, edge.target_columns)
            position = -1
            while True:
                check()
                with self.database.connect() as db:
                    db.execute(
                        "UPDATE graph_draft_runs SET touched=UNIX_TIMESTAMP() WHERE version=%s",
                        (version,),
                    )
                    if edge.mode == "same_row":
                        db.execute(
                            """SELECT a.position,a.entity_id,b.entity_id FROM graph_draft_bindings a
                            JOIN graph_draft_bindings b ON b.version=a.version AND b.binding=%s AND b.position=a.position
                            WHERE a.version=%s AND a.binding=%s AND a.position>%s ORDER BY a.position LIMIT 500""",
                            (b.id, version, a.id, position),
                        )
                    else:
                        db.execute(
                            """SELECT a.position,s.entity_id,t.entity_id FROM staging_keys a
                            JOIN staging_keys b ON b.key_set=%s AND b.digest=a.digest AND b.value=a.value
                            JOIN graph_draft_bindings s ON s.version=%s AND s.binding=%s AND s.position=a.position
                            JOIN graph_draft_bindings t ON t.version=%s AND t.binding=%s AND t.position=b.position
                            WHERE a.key_set=%s AND a.position>%s ORDER BY a.position LIMIT 500""",
                            (target_set, version, a.id, version, b.id, source_set, position),
                        )
                    pairs = db.fetchall()
                if not pairs:
                    break
                records = []
                position = pairs[-1][0]
                for _, source, target in pairs:
                    edge_id = hashlib.sha256(encoded_key([edge.id, source, target])).hexdigest()
                    records.append(
                        (
                            version,
                            edge_id,
                            dumps(
                                {
                                    "source": source,
                                    "target": target,
                                    "relation_id": edge.id,
                                    "origin": "confirmed",
                                    "name": edge.name,
                                }
                            ),
                        )
                    )
                with self.database.connect() as db:
                    db.executemany("INSERT IGNORE INTO graph_draft_edges VALUES(%s,%s,%s)", records)
        return version

    def rows(self, version, kind):
        table = {"nodes": "graph_draft_nodes", "edges": "graph_draft_edges"}[kind]
        after = ""
        while True:
            with self.database.connect() as db:
                db.execute(
                    "UPDATE graph_draft_runs SET touched=UNIX_TIMESTAMP() WHERE version=%s",
                    (version,),
                )
                db.execute(
                    f"SELECT id,data FROM {table} WHERE version=%s AND id>%s ORDER BY id LIMIT 64",
                    (version, after),
                )
                rows = db.fetchall()
            if not rows:
                return
            after = rows[-1][0]
            for _, data in rows:
                yield json.loads(data)


def merge_fields(target, incoming, name):
    for key, value in incoming.items():
        if key in target and target[key] != value:
            raise AlignmentError(
                f"同一「{name}」的属性「{key}」出现不同值，请核对身份范围、属性归属或时间维度；未发布图谱"
            )
        target[key] = value
