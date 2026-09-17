"""Module interfaces. Implementations are chosen only in bootstrap."""

from typing import Protocol

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
    def ingest(self, request: ImportRequest) -> SourceArtifact: ...


class Extractor(Protocol):
    def extract(self, request: ImportRequest, artifact: SourceArtifact) -> ExtractionBatch: ...


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
