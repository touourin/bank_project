from dataclasses import dataclass

from bank_project.ports import HealthCheck, PipelineHandler, QueryHandler


@dataclass(frozen=True)
class ApplicationServices:
    pipeline: PipelineHandler
    query: QueryHandler
    storage: HealthCheck | None
    graph_backend: str
