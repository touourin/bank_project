from datetime import UTC, datetime

from bank_project.contracts.errors import Conflict, DependencyUnavailable, IntakeError, NotFound
from bank_project.contracts.intake import (
    ExtractionRequest,
    ExtractionState,
    ExtractionSummary,
    MappingSummary,
    StoredExtraction,
)
from bank_project.contracts.integrity import validate_batch
from bank_project.contracts.mapping import MappingCatalog
from bank_project.contracts.models import ExtractionBatch
from bank_project.extraction.documents import DocumentExtractor
from bank_project.extraction.structured import StructuredExtractor
from bank_project.ports import DocumentCache, DocumentModel, PreparationStore, RawStore

PRODUCER = "bank-preparation/1.1"


class ExtractionService:
    """Stage two only. Atomic publication, explicit failures and retryable model chunks."""

    def __init__(
        self,
        store: PreparationStore,
        raw: RawStore,
        catalog: MappingCatalog,
        model: DocumentModel,
        cache: DocumentCache,
    ):
        self.store, self.raw = store, raw
        self.structured = StructuredExtractor(catalog)
        self.documents = DocumentExtractor(model, cache)

    def extract(self, request: ExtractionRequest) -> ExtractionSummary:
        dataset_id, batch_id = request.dataset_id, request.batch_id
        with self.store.lock(dataset_id, batch_id):
            source = self.store.load_import(dataset_id, batch_id)
            if source is None:
                raise NotFound("请先导入该批次")
            tabular = source.parsed.kind == "table"
            if not tabular and request.mapping is not None:
                raise IntakeError("文档抽取不使用表格映射 mapping")
            configuration = (
                self.structured.digest(source, request.mapping)
                if tabular
                else self.documents.configuration_digest
            )
            existing = self.store.load_extraction(dataset_id, batch_id)
            if existing is not None:
                if (
                    existing.summary.configuration_digest != configuration
                    or existing.summary.producer_version != PRODUCER
                ):
                    raise Conflict("转换配置已变化；为保留历史结果，请以新的 batch_id 重新导入")
                return existing.summary
            self.store.save_state(
                ExtractionState(dataset_id=dataset_id, batch_id=batch_id, status="running")
            )
            try:
                self.raw.read(source.receipt.artifact)
                if tabular:
                    batch, warnings = self.structured.extract(source, PRODUCER, request.mapping)
                else:
                    batch, warnings = self.documents.extract(source, PRODUCER)
                validate_batch(batch)
                summary = ExtractionSummary(
                    dataset_id=dataset_id,
                    batch_id=batch_id,
                    entities=len(batch.entities),
                    events=len(batch.events),
                    relations=len(batch.relations),
                    evidence=len(batch.evidence),
                    input_digest=source.receipt.artifact.digest,
                    configuration_digest=configuration,
                    producer_version=PRODUCER,
                    mapping=self.structured.select(source, request.mapping).id if tabular else None,
                    warnings=warnings,
                    completed_at=datetime.now(UTC),
                )
                # The output and summary share one commit point; never publish partial candidates.
                self.store.save_extraction(StoredExtraction(summary=summary, batch=batch))
                return summary
            except Exception as exc:
                detail = (
                    str(exc) if isinstance(exc, IntakeError) else "转换失败；未发布部分结果，可重试"
                )
                self.store.save_state(
                    ExtractionState(
                        dataset_id=dataset_id, batch_id=batch_id, status="failed", detail=detail
                    )
                )
                if isinstance(exc, IntakeError):
                    raise
                raise DependencyUnavailable(detail) from exc

    def result(self, dataset_id: str, batch_id: str) -> ExtractionBatch:
        result = self.store.load_extraction(dataset_id, batch_id)
        if result is None:
            raise NotFound("没有已完成的转换结果")
        return result.batch

    def mappings(self) -> list[MappingSummary]:
        return [
            MappingSummary(id=p.id, version=p.version, table=p.table, match_columns=p.match_columns)
            for p in self.structured.catalog.profiles
        ]

    def state(self, dataset_id: str, batch_id: str) -> ExtractionState:
        result = self.store.load_extraction(dataset_id, batch_id)
        if result is not None:
            return ExtractionState(
                dataset_id=dataset_id, batch_id=batch_id, status="completed", summary=result.summary
            )
        if self.store.load_import(dataset_id, batch_id) is None:
            raise NotFound("导入批次不存在")
        state = self.store.load_state(dataset_id, batch_id)
        if state is not None and state.status == "running":
            try:
                with self.store.lock(dataset_id, batch_id):
                    # A prior worker died. Its lock was released by the OS, so retry is safe.
                    completed = self.store.load_extraction(dataset_id, batch_id)
                    if completed is not None:
                        return ExtractionState(
                            dataset_id=dataset_id,
                            batch_id=batch_id,
                            status="completed",
                            summary=completed.summary,
                        )
                    state = ExtractionState(
                        dataset_id=dataset_id,
                        batch_id=batch_id,
                        status="failed",
                        detail="上次处理已中断，可重试",
                    )
                    self.store.save_state(state)
            except Conflict:
                pass
        return state or ExtractionState(
            dataset_id=dataset_id, batch_id=batch_id, status="not_started"
        )
