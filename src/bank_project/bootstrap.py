"""Composition root: replace placeholders here when business development begins."""

from bank_project.application import ApplicationServices
from bank_project.pipeline.service import Pipeline
from bank_project.ports import HealthCheck
from bank_project.query.service import QueryService
from bank_project.settings import Settings


def build_services(settings: Settings) -> ApplicationServices:
    storage: HealthCheck | None = None
    if settings.graph_backend == "neo4j":
        from bank_project.adapters.graph_store.connection import Neo4jConnection

        # Settings validates credentials before any infrastructure is constructed.
        assert settings.neo4j_password is not None
        storage = Neo4jConnection(
            settings.neo4j_uri,
            settings.neo4j_user,
            settings.neo4j_password.get_secret_value(),
            settings.neo4j_database,
            timeout=settings.health_timeout_seconds,
        )
    return ApplicationServices(
        pipeline=Pipeline(),
        query=QueryService(),
        storage=storage,
        graph_backend=settings.graph_backend,
    )
