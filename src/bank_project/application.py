from dataclasses import dataclass

from bank_project.ports import Extractor, HealthCheck, Ingestor, PipelineHandler, QueryHandler


@dataclass(frozen=True)
class ApplicationServices:
    pipeline: PipelineHandler
    query: QueryHandler
    storage: HealthCheck | None
    graph_backend: str
    ingestion: Ingestor | None = None
    extraction: Extractor | None = None
