"""Immutable batches with bounded writes, keyset reads and atomic publication."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from bank_project.intake.models import (
    BatchDetail,
    BatchInfo,
    BatchPage,
    DataRow,
    IntakeError,
    TableInfo,
    TablePage,
)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class MysqlBatchStore:
    def __init__(self, database):
        self.database = database
        with database.connect() as db:
            for sql in (
                """CREATE TABLE IF NOT EXISTS intake_batches (
                    id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
                    created_at VARCHAR(40) NOT NULL, state VARCHAR(16) NOT NULL,
                    deleted BOOLEAN NOT NULL DEFAULT FALSE, metadata JSON NOT NULL,
                    INDEX batch_listing(state,deleted,created_at,id)) ENGINE=InnoDB""",
                """CREATE TABLE IF NOT EXISTS intake_tables (
                    id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
                    batch_id CHAR(36) CHARACTER SET ascii NOT NULL,
                    position INT NOT NULL, metadata JSON NOT NULL,
                    INDEX tables_batch(batch_id,position),
                    FOREIGN KEY (batch_id) REFERENCES intake_batches(id) ON DELETE CASCADE) ENGINE=InnoDB""",
                """CREATE TABLE IF NOT EXISTS intake_rows (
                    table_id CHAR(36) CHARACTER SET ascii NOT NULL, position BIGINT NOT NULL,
                    source_number BIGINT NOT NULL, cells JSON NOT NULL,
                    PRIMARY KEY(table_id,position),
                    FOREIGN KEY (table_id) REFERENCES intake_tables(id) ON DELETE CASCADE) ENGINE=InnoDB""",
            ):
                db.execute(sql)

    def collect_failed(self):
        # Limit each cleanup transaction; never touch ready (including hidden) snapshots.
        with self.database.connect() as db:
            db.execute(
                "SELECT id FROM intake_batches WHERE state='failed' ORDER BY created_at LIMIT 1"
            )
            batch = db.fetchone()
            if not batch:
                return False
            db.execute("SELECT id FROM intake_tables WHERE batch_id=%s LIMIT 1", (batch[0],))
            table = db.fetchone()
            if table:
                db.execute("DELETE FROM intake_rows WHERE table_id=%s LIMIT 1000", (table[0],))
                if not db.rowcount:
                    db.execute("DELETE FROM intake_tables WHERE id=%s", (table[0],))
            else:
                db.execute("DELETE FROM intake_batches WHERE id=%s AND state='failed'", (batch[0],))
            return True

    def writer(self, *, name, source_kind, source, sha256=None, batch_id=None):
        return BatchWriter(
            self,
            BatchDetail(
                id=batch_id or str(uuid4()),
                name=name,
                source_kind=source_kind,
                source=source,
                created_at=datetime.now(UTC).isoformat(),
                table_count=0,
                row_count=0,
                sha256=sha256,
                tables=[],
            ),
        )

    def save(self, parsed, **metadata):
        with self.writer(**metadata) as writer:
            for table in parsed.tables:
                writer.start(table.name, table.columns, table.foreign_keys)
                for row in table.rows:
                    writer.append(row)
                writer.finish(table.columns, table.warnings)
            return writer.publish(parsed.warnings)

    def list(self, offset, limit):
        with self.database.connect() as db:
            db.execute("SELECT COUNT(*) FROM intake_batches WHERE state='ready' AND deleted=FALSE")
            total = db.fetchone()[0]
            db.execute(
                "SELECT metadata FROM intake_batches WHERE state='ready' AND deleted=FALSE ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
            items = [BatchInfo.model_validate_json(r[0]) for r in db.fetchall()]
        return BatchPage(items=items, total=total, offset=offset, limit=limit)

    def get(self, batch_id, *, include_deleted=False):
        with self.database.connect() as db:
            db.execute(
                "SELECT metadata,deleted FROM intake_batches WHERE id=%s AND state='ready'",
                (batch_id,),
            )
            row = db.fetchone()
            if not row or (row[1] and not include_deleted):
                raise IntakeError("批次不存在或已被删除", status=404)
            db.execute(
                "SELECT metadata FROM intake_tables WHERE batch_id=%s ORDER BY position",
                (batch_id,),
            )
            return BatchDetail(
                **json.loads(row[0]),
                tables=[TableInfo.model_validate_json(r[0]) for r in db.fetchall()],
            )

    def iter_rows(self, table_id, *, start=0, batch_size=64):
        # No long read transaction and no OFFSET scan: ready batches are immutable.
        position = start - 1
        while True:
            with self.database.connect() as db:
                db.execute(
                    "SELECT position,source_number,cells FROM intake_rows WHERE table_id=%s AND position>%s ORDER BY position LIMIT %s",
                    (table_id, position, batch_size),
                )
                records = db.fetchall()
            if not records:
                return
            for position, number, cells in records:
                yield position, DataRow(number=number, values=json.loads(cells))

    def samples(self, table):
        positions = (
            sorted({i * (table.row_count - 1) // 4 for i in range(5)}) if table.row_count else []
        )
        if not positions:
            return []
        with self.database.connect() as db:
            placeholders = ",".join(["%s"] * len(positions))
            db.execute(
                f"SELECT source_number,cells FROM intake_rows WHERE table_id=%s AND position IN ({placeholders}) ORDER BY position",
                (table.id, *positions),
            )
            return [
                DataRow(
                    number=r[0], values=[None if v is None else v[:80] for v in json.loads(r[1])]
                )
                for r in db.fetchall()
            ]

    def preview(self, batch_id, table_id, offset, limit):
        batch = self.get(batch_id)
        table = next((t for t in batch.tables if t.id == table_id), None)
        if table is None:
            raise IntakeError("数据表不存在或不属于该批次", status=404)
        with self.database.connect() as db:
            db.execute(
                "SELECT source_number,cells FROM intake_rows WHERE table_id=%s AND position>=%s ORDER BY position LIMIT %s",
                (table_id, offset, limit),
            )
            rows = [DataRow(number=r[0], values=json.loads(r[1])) for r in db.fetchall()]
        return TablePage(**table.model_dump(), rows=rows, offset=offset, limit=limit)

    def delete(self, batch_id):
        self.get(batch_id)
        # Logical deletion keeps immutable data available to historical mapping versions.
        with self.database.connect() as db:
            db.execute("UPDATE intake_batches SET deleted=TRUE WHERE id=%s", (batch_id,))


class BatchWriter:
    def __init__(self, store, batch):
        self.store, self.batch = store, batch
        self.buffer, self.buffer_bytes = [], 0
        self.current, self.count = None, 0
        self.published = False
        self.progress = lambda count: None

    def __enter__(self):
        with self.store.database.connect() as db:
            db.execute(
                "INSERT INTO intake_batches(id,created_at,state,metadata) VALUES(%s,%s,'building',%s)",
                (
                    self.batch.id,
                    self.batch.created_at,
                    dumps(self.batch.model_dump(exclude={"tables"})),
                ),
            )
        return self

    def start(self, name, columns, foreign_keys=(), *, table_id=None):
        if self.current is not None:
            raise RuntimeError("Previous table not finished")
        self.current = TableInfo(
            id=table_id or str(uuid4()),
            name=name,
            row_count=0,
            columns=columns,
            foreign_keys=list(foreign_keys),
        )
        self.count = 0
        with self.store.database.connect() as db:
            db.execute(
                "INSERT INTO intake_tables VALUES(%s,%s,%s,%s)",
                (
                    self.current.id,
                    self.batch.id,
                    len(self.batch.tables),
                    self.current.model_dump_json(),
                ),
            )

    def append(self, row):
        content = dumps(row.values)
        size = len(content.encode("utf-8"))
        if self.buffer and (len(self.buffer) >= 250 or self.buffer_bytes + size > 1024 * 1024):
            self.flush()
        self.buffer.append((self.current.id, self.count, row.number, content))
        self.buffer_bytes += size
        self.count += 1

    def flush(self):
        if self.buffer:
            with self.store.database.connect() as db:
                db.executemany("INSERT INTO intake_rows VALUES(%s,%s,%s,%s)", self.buffer)
            self.buffer, self.buffer_bytes = [], 0
            self.progress(self.batch.row_count + self.count)

    def finish(self, columns, warnings=()):
        self.flush()
        self.current.columns, self.current.warnings = columns, list(warnings)
        self.current.row_count = self.count
        with self.store.database.connect() as db:
            db.execute(
                "UPDATE intake_tables SET metadata=%s WHERE id=%s",
                (self.current.model_dump_json(), self.current.id),
            )
        self.batch.tables.append(self.current)
        self.batch.row_count += self.count
        self.current = None

    def publish(self, warnings=()):
        if self.current is not None:
            raise RuntimeError("Table not finished")
        self.batch.table_count, self.batch.warnings = len(self.batch.tables), list(warnings)
        with self.store.database.connect() as db:
            db.execute(
                "UPDATE intake_batches SET state='ready',created_at=%s,metadata=%s WHERE id=%s AND state='building'",
                (
                    self.batch.created_at,
                    dumps(self.batch.model_dump(exclude={"tables"})),
                    self.batch.id,
                ),
            )
        self.published = True
        return self.batch

    def __exit__(self, *args):
        # Failed attempts stay invisible. Bounded garbage collection is performed by the worker.
        if not self.published:
            with self.store.database.connect() as db:
                db.execute(
                    "UPDATE intake_batches SET state='failed' WHERE id=%s AND state='building'",
                    (self.batch.id,),
                )
