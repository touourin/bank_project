import hashlib
from datetime import UTC, datetime

from bank_project.contracts.errors import Conflict, IntakeError, NotFound
from bank_project.contracts.intake import IngestionReceipt, RawInput, StoredImport
from bank_project.contracts.models import ImportRequest
from bank_project.ports import PreparationStore, RawStore, SourceParser, SourceReader


class IngestionService:
    """Stage one only: read, parse and durably record an immutable source snapshot."""

    def __init__(
        self, reader: SourceReader, parser: SourceParser, raw: RawStore, store: PreparationStore
    ):
        self.reader, self.parser, self.raw, self.store = reader, parser, raw, store

    def ingest(self, request: ImportRequest, upload: RawInput | None = None) -> IngestionReceipt:
        with self.store.lock(request.dataset_id, request.batch_id):
            if upload is not None and request.source_uri != f"upload:{upload.filename}":
                raise IntakeError("上传文件与来源标识不一致")
            source = upload if upload is not None else self.reader.read(request)
            existing = self.store.load_import(request.dataset_id, request.batch_id)
            if existing is not None:
                if (
                    existing.request != request
                    or existing.receipt.artifact.digest
                    != hashlib.sha256(source.content).hexdigest()
                ):
                    raise Conflict("批次已存在且来源或内容不同；请使用新的 batch_id")
                return existing.receipt
            parsed = self.parser.parse(source, request)
            artifact = self.raw.save(source.content)
            receipt = IngestionReceipt(
                dataset_id=request.dataset_id,
                batch_id=request.batch_id,
                source_system=request.source_system,
                artifact=artifact,
                filename=parsed.filename,
                table=parsed.table,
                records=len(parsed.rows) if parsed.kind == "table" else len(parsed.blocks),
                imported_at=datetime.now(UTC),
                warnings=parsed.warnings,
            )
            # A complete manifest is the commit point. A crash can leave only an unreferenced blob.
            self.store.save_import(StoredImport(request=request, receipt=receipt, parsed=parsed))
            return receipt

    def get(self, dataset_id: str, batch_id: str) -> IngestionReceipt:
        value = self.store.load_import(dataset_id, batch_id)
        if value is None:
            raise NotFound("导入批次不存在")
        return value.receipt
