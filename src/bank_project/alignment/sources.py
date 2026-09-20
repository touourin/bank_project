"""Read schema and samples from immutable MySQL batches; retain the small SQLite fallback."""

import json

from bank_project.intake.models import BatchInfo, DataRow, TableInfo
from bank_project.intake.store import BatchStore

from .models import AlignmentError, Selection, SourceTable


class StagedSources:
    def __init__(self, store: BatchStore, max_cells: int, max_bytes: int = 100 * 1024 * 1024):
        self.store, self.max_cells, self.max_bytes = store, max_cells, max_bytes

    def read(self, selections: list[Selection]) -> list[SourceTable]:
        if hasattr(self.store, "samples"):
            sources = []
            for selection in selections:
                batch = self.store.get(str(selection.batch_id))
                table = next((t for t in batch.tables if t.id == str(selection.table_id)), None)
                if table is None:
                    raise AlignmentError("所选数据表不存在或不属于该批次", 404)
                sources.append(
                    SourceTable(
                        batch=batch, table=table, rows=self.store.samples(table), staged=True
                    )
                )
            return sources
        sources, cells, byte_count = [], 0, 0
        with self.store.connect() as db:
            db.execute("BEGIN")
            for selection in selections:
                record = db.execute(
                    "SELECT b.metadata, t.metadata FROM tables t JOIN batches b ON b.id=t.batch_id "
                    "WHERE t.id=? AND b.id=? AND b.deleted=0",
                    (str(selection.table_id), str(selection.batch_id)),
                ).fetchone()
                if record is None:
                    raise AlignmentError("所选数据表已删除或不属于该批次，请重新选择", 404)
                batch, table = (
                    BatchInfo.model_validate_json(record[0]),
                    TableInfo.model_validate_json(record[1]),
                )
                cells += table.row_count * len(table.columns)
                if cells > self.max_cells:
                    raise AlignmentError("所选表合计单元格过多，请分批分析", 413)
                rows = []
                for number, content in db.execute(
                    "SELECT source_number,cells FROM rows WHERE table_id=? ORDER BY position",
                    (table.id,),
                ):
                    byte_count += len(content.encode("utf-8"))
                    if byte_count > self.max_bytes:
                        raise AlignmentError("所选表合计内容超过 100 MB，请分批分析", 413)
                    rows.append(DataRow(number=number, values=json.loads(content)))
                sources.append(SourceTable(batch=batch, table=table, rows=rows))
        return sources
