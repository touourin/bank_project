"""Transactional local batches. Each write publishes an entire source or nothing."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from bank_project.intake.models import (
    BatchDetail,
    BatchInfo,
    BatchPage,
    DataRow,
    IntakeError,
    ParsedSource,
    TableInfo,
    TablePage,
)


class BatchStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tables (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL, metadata TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tables_batch ON tables(batch_id, position);
                CREATE TABLE IF NOT EXISTS rows (
                    table_id TEXT NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL, source_number INTEGER NOT NULL, cells TEXT NOT NULL,
                    PRIMARY KEY(table_id, position)
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(
        self,
        parsed: ParsedSource,
        *,
        name: str,
        source_kind: str,
        source: str,
        sha256: str | None = None,
    ) -> BatchDetail:
        batch = BatchDetail(
            id=str(uuid4()),
            name=name,
            source_kind=source_kind,
            source=source,
            created_at=datetime.now(UTC).isoformat(),
            sha256=sha256,
            table_count=len(parsed.tables),
            row_count=sum(len(t.rows) for t in parsed.tables),
            tables=[],
            warnings=parsed.warnings,
        )
        with self.connect() as db:
            db.execute(
                "INSERT INTO batches VALUES (?, ?, ?)",
                (
                    batch.id,
                    batch.created_at,
                    BatchInfo.model_validate(
                        batch.model_dump(exclude={"tables"})
                    ).model_dump_json(),
                ),
            )
            for position, table in enumerate(parsed.tables):
                info = TableInfo(
                    id=str(uuid4()),
                    name=table.name,
                    row_count=len(table.rows),
                    columns=table.columns,
                    foreign_keys=table.foreign_keys,
                    warnings=table.warnings,
                )
                batch.tables.append(info)
                db.execute(
                    "INSERT INTO tables VALUES (?, ?, ?, ?)",
                    (info.id, batch.id, position, info.model_dump_json()),
                )
                db.executemany(
                    "INSERT INTO rows VALUES (?, ?, ?, ?)",
                    (
                        (info.id, i, row.number, json.dumps(row.values, ensure_ascii=False))
                        for i, row in enumerate(table.rows)
                    ),
                )
        return batch

    def list(self, offset: int, limit: int) -> BatchPage:
        with self.connect() as db:
            db.execute("BEGIN")
            total = db.execute("SELECT count(*) FROM batches").fetchone()[0]
            records = db.execute(
                "SELECT metadata FROM batches ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return BatchPage(
            items=[BatchInfo.model_validate_json(row[0]) for row in records],
            total=total,
            offset=offset,
            limit=limit,
        )

    def get(self, batch_id: str) -> BatchDetail:
        with self.connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT metadata FROM batches WHERE id=?", (batch_id,)).fetchone()
            if row is None:
                raise IntakeError("批次不存在或已被删除", status=404)
            tables = db.execute(
                "SELECT metadata FROM tables WHERE batch_id=? ORDER BY position", (batch_id,)
            ).fetchall()
        return BatchDetail(
            **json.loads(row[0]), tables=[TableInfo.model_validate_json(t[0]) for t in tables]
        )

    def preview(self, batch_id: str, table_id: str, offset: int, limit: int) -> TablePage:
        with self.connect() as db:
            db.execute("BEGIN")
            table = db.execute(
                "SELECT metadata FROM tables WHERE batch_id=? AND id=?", (batch_id, table_id)
            ).fetchone()
            if table is None:
                raise IntakeError("数据表不存在或不属于该批次", status=404)
            rows = db.execute(
                "SELECT source_number, cells FROM rows WHERE table_id=? ORDER BY position LIMIT ? OFFSET ?",
                (table_id, limit, offset),
            ).fetchall()
        return TablePage(
            **json.loads(table[0]),
            rows=[DataRow(number=r[0], values=json.loads(r[1])) for r in rows],
            offset=offset,
            limit=limit,
        )

    def delete(self, batch_id: str) -> None:
        with self.connect() as db:
            if db.execute("DELETE FROM batches WHERE id=?", (batch_id,)).rowcount == 0:
                raise IntakeError("批次不存在或已被删除", status=404)
