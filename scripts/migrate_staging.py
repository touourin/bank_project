"""Copy legacy SQLite batches without changing IDs or deleting the original database."""

import argparse
import json
import sqlite3
from pathlib import Path

from bank_project.intake.models import BatchInfo, DataRow, TableInfo
from bank_project.settings import Settings
from bank_project.staging.database import StagingDatabase
from bank_project.staging.store import MysqlBatchStore


def migrate(source, store):
    with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        for (metadata,) in db.execute("SELECT metadata FROM batches ORDER BY created_at,id"):
            batch = BatchInfo.model_validate_json(metadata)
            with store.database.connect() as destination:
                destination.execute("SELECT state FROM intake_batches WHERE id=%s", (batch.id,))
                existing = destination.fetchone()
                if existing:
                    if existing[0] != "ready":
                        raise RuntimeError(
                            "An earlier incomplete migration needs cleanup before retry"
                        )
                    destination.execute(
                        "UPDATE intake_batches SET created_at=%s WHERE id=%s",
                        (batch.created_at, batch.id),
                    )
                    continue
            with store.writer(
                name=batch.name,
                source_kind=batch.source_kind,
                source=batch.source,
                sha256=batch.sha256,
                batch_id=batch.id,
            ) as writer:
                writer.batch.created_at = batch.created_at
                for (record,) in db.execute(
                    "SELECT metadata FROM tables WHERE batch_id=? ORDER BY position", (batch.id,)
                ):
                    table = TableInfo.model_validate_json(record)
                    writer.start(table.name, table.columns, table.foreign_keys, table_id=table.id)
                    for number, cells in db.execute(
                        "SELECT source_number,cells FROM rows WHERE table_id=? ORDER BY position",
                        (table.id,),
                    ):
                        writer.append(DataRow(number=number, values=json.loads(cells)))
                    writer.finish(table.columns, table.warnings)
                    if writer.batch.tables[-1].row_count != table.row_count:
                        raise RuntimeError("Source row count mismatch")
                writer.publish(batch.warnings)
            print(f"Migrated {batch.id}: {batch.row_count} rows", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("sqlite", type=Path)
    args = parser.parse_args()
    migrate(args.sqlite, MysqlBatchStore(StagingDatabase(Settings())))
