"""Composition root: bind generic services to configurable infrastructure adapters."""

from bank_project.adapters.mappings import load_mappings
from bank_project.adapters.model_client.compatible import CompatibleModel, DisabledModel
from bank_project.adapters.parsing.parser import FileParser
from bank_project.adapters.raw_store.filesystem import FileStore
from bank_project.adapters.sources.files import InboxReader
from bank_project.adapters.sources.mysql import MySQLReader
from bank_project.adapters.sources.router import SourceRouter
from bank_project.application import ApplicationServices
from bank_project.contracts.schema import IntakeLimits
from bank_project.extraction.service import ExtractionService
from bank_project.ingestion.service import IngestionService
from bank_project.pipeline.service import Pipeline
from bank_project.ports import HealthCheck
from bank_project.query.service import QueryService
from bank_project.settings import Settings


def build_services(settings: Settings) -> ApplicationServices:
    limits = IntakeLimits(file_bytes=settings.max_file_bytes, rows=settings.max_records)
    store = FileStore(settings.data_dir)
    mysql = None
    if settings.mysql_source_enabled:
        mysql = MySQLReader(
            settings.mysql_source_tables,
            limits,
            {
                "host": settings.mysql_host,
                "port": settings.mysql_port,
                "user": settings.mysql_user,
                "database": settings.mysql_database,
                "password": settings.mysql_password.get_secret_value(),
            },
        )
    reader = SourceRouter(InboxReader(settings.import_root, limits.file_bytes), mysql)
    model = DisabledModel()
    if settings.model_enabled:
        model = CompatibleModel(
            settings.model_base_url,
            settings.model_name,
            settings.model_api_key.get_secret_value() if settings.model_api_key else "",
            settings.model_timeout_seconds,
            prompt=settings.model_prompt_file.read_text(encoding="utf-8")
            if settings.model_prompt_file
            else None,
        )
    storage: HealthCheck | None = None
    if settings.neo4j_enabled:
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
        graph_backend="neo4j" if settings.neo4j_enabled else "none",
        ingestion=IngestionService(reader, FileParser(limits), store, store),
        extraction=ExtractionService(
            store, store, load_mappings(settings.mapping_dir), model, store
        ),
    )
