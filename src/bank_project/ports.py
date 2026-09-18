"""Module interfaces. Implementations are chosen only in bootstrap."""

from contextlib import AbstractContextManager
from typing import Protocol

from bank_project.contracts.document import DocumentExtraction
from bank_project.contracts.intake import (
    ExtractionRequest,
    ExtractionState,
    ExtractionSummary,
    IngestionReceipt,
    MappingSummary,
    ParsedSource,
    RawInput,
    StoredExtraction,
    StoredImport,
)
from bank_project.contracts.models import (
    EvidenceResult,
    ExtractionBatch,
    GraphPatch,
    GraphQuery,
    ImportRequest,
    QueryResult,
    ResolutionResult,
    RunResult,
    SemanticReport,
    SourceArtifact,
)


class RawStore(Protocol):
    def save(self, content: bytes) -> SourceArtifact: ...

    def read(self, artifact: SourceArtifact) -> bytes: ...


class Ingestor(Protocol):
    def ingest(
        self, request: ImportRequest, upload: RawInput | None = None
    ) -> IngestionReceipt: ...

    def get(self, dataset_id: str, batch_id: str) -> IngestionReceipt: ...


class Extractor(Protocol):
    def extract(self, request: ExtractionRequest) -> ExtractionSummary: ...

    def result(self, dataset_id: str, batch_id: str) -> ExtractionBatch: ...

    def state(self, dataset_id: str, batch_id: str) -> ExtractionState: ...

    def mappings(self) -> list[MappingSummary]: ...


class SourceReader(Protocol):
    def read(self, request: ImportRequest) -> RawInput: ...


class SourceParser(Protocol):
    def parse(self, source: RawInput, request: ImportRequest) -> ParsedSource: ...


class PreparationStore(Protocol):
    def lock(self, dataset_id: str, batch_id: str) -> AbstractContextManager[None]: ...

    def load_import(self, dataset_id: str, batch_id: str) -> StoredImport | None: ...

    def save_import(self, value: StoredImport) -> None: ...

    def load_extraction(self, dataset_id: str, batch_id: str) -> StoredExtraction | None: ...

    def save_extraction(self, value: StoredExtraction) -> None: ...

    def load_state(self, dataset_id: str, batch_id: str) -> ExtractionState | None: ...

    def save_state(self, value: ExtractionState) -> None: ...


class DocumentModel(Protocol):
    fingerprint: str

    def extract(self, text: str) -> DocumentExtraction: ...


class DocumentCache(Protocol):
    def get(self, key: str) -> DocumentExtraction | None: ...

    def put(self, key: str, value: DocumentExtraction) -> None: ...


class Resolver(Protocol):
    def resolve(self, batch: ExtractionBatch) -> ResolutionResult: ...


class SemanticValidator(Protocol):
    def validate(self, batch: ExtractionBatch) -> SemanticReport: ...


class GraphBuilder(Protocol):
    def build(self, batch: ExtractionBatch, resolution: ResolutionResult) -> GraphPatch: ...


class GraphWriter(Protocol):
    def publish(self, patch: GraphPatch, run: RunResult) -> None: ...


class GraphReader(Protocol):
    def query(self, request: GraphQuery) -> QueryResult: ...

    def evidence(self, dataset_id: str, evidence_id: str) -> EvidenceResult: ...


class RunReader(Protocol):
    def get_run(self, dataset_id: str, run_id: str) -> RunResult | None: ...


class QueryHandler(GraphReader, Protocol):
    pass


class PipelineHandler(Protocol):
    def import_data(self, request: ImportRequest) -> RunResult: ...

    def run(self, batch: ExtractionBatch) -> RunResult: ...

    def get_run(self, dataset_id: str, run_id: str) -> RunResult: ...


class HealthCheck(Protocol):
    async def ready(self) -> bool: ...

    async def close(self) -> None: ...
