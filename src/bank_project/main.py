"""Application factory for the backend foundation."""

import sqlite3
from contextlib import asynccontextmanager

import anyio
import pymysql
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from bank_project import __version__
from bank_project.alignment.analyzer import Analyzer
from bank_project.alignment.graph import VersionedGraph
from bank_project.alignment.model_client import JsonModel
from bank_project.alignment.models import AlignmentError
from bank_project.alignment.retrieval import RetrieveClient
from bank_project.alignment.service import AlignmentService
from bank_project.alignment.sources import StagedSources
from bank_project.alignment.store import RunStore
from bank_project.api.alignment import router as alignment_router
from bank_project.api.body_limit import BodyLimitMiddleware
from bank_project.api.graphrag import router as graphrag_router
from bank_project.api.health import router as health_router
from bank_project.api.intake import router as intake_router
from bank_project.api.knowledge import router as knowledge_router
from bank_project.api.ontology import router as ontology_router
from bank_project.api.resolution import router as resolution_router
from bank_project.api.risk import router as risk_router
from bank_project.graphrag import GraphRagError, GraphRagService
from bank_project.intake.lifecycle import BatchLifecycle
from bank_project.intake.models import IntakeError
from bank_project.intake.mysql import MysqlConnection, MysqlSource
from bank_project.intake.service import IntakeService
from bank_project.intake.store import BatchStore
from bank_project.knowledge.service import KnowledgeService
from bank_project.ontology.service import OntologyService
from bank_project.resolution.service import ResolutionService
from bank_project.risk.service import RiskService
from bank_project.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings if settings is not None else Settings()
        config = app.state.settings
        app.state.ontology = OntologyService(config)
        limits = config.intake_limits()
        configured_mysql = (
            MysqlConnection(
                host=config.mysql_host,
                port=config.mysql_port,
                database=config.mysql_database,
                user=config.mysql_user,
                password=config.mysql_password,
            )
            if config.mysql_password
            else None
        )
        from bank_project.staging.database import StagingDatabase
        from bank_project.staging.jobs import IntakeJobs
        from bank_project.staging.keys import KeyIndex
        from bank_project.staging.store import MysqlBatchStore

        database = StagingDatabase(config) if config.staging_backend == "mysql" else None
        batch_store = (
            MysqlBatchStore(database)
            if database
            else BatchStore(config.data_dir / "intake" / "batches.sqlite3")
        )
        key_index = KeyIndex(batch_store) if database else None
        app.state.intake_jobs = IntakeJobs(database) if database else None
        app.state.upload_slots = anyio.Semaphore(2)
        app.state.intake = IntakeService(
            batch_store,
            limits,
            MysqlSource(limits),
            configured_mysql,
        )
        app.state.alignment = AlignmentService(
            config,
            StagedSources(app.state.intake.store, config.intake_max_cells),
            RunStore(config.data_dir / "alignment" / "runs.sqlite3"),
            Analyzer(
                JsonModel(config),
                RetrieveClient(
                    config.retrieve_base_url,
                    config.retrieve_timeout_seconds,
                    config.retrieve_concurrency,
                ),
                config.alignment_min_confidence,
                key_index,
                batch_columns=config.alignment_batch_columns,
                model_concurrency=config.alignment_model_concurrency,
            ),
            VersionedGraph(config, batch_store if database else None, key_index),
            ontology=app.state.ontology,
        )
        app.state.batch_lifecycle = BatchLifecycle(batch_store, app.state.alignment.store)
        app.state.graphrag = GraphRagService(config)
        app.state.knowledge = KnowledgeService(
            config, app.state.graphrag, app.state.alignment.graph, ontology=app.state.ontology
        )
        app.state.resolution = ResolutionService(config, app.state.knowledge.load_graph)
        app.state.knowledge.resolution = app.state.resolution
        app.state.risk = RiskService(config, app.state.alignment.graph, ontology=app.state.ontology)
        try:
            yield
        finally:
            await app.state.risk.close()
            await app.state.alignment.close()
            await app.state.knowledge.close()
            await app.state.resolution.close()

    app = FastAPI(
        title="Bank project",
        version=__version__,
        description="数据接入、本体对齐与图谱生成、TXT GraphRAG 索引问答、实体消歧、BOID 挂载及 WHY 风险规则审核执行。",
        lifespan=lifespan,
    )
    app.add_middleware(BodyLimitMiddleware)
    app.include_router(health_router)
    app.include_router(intake_router)
    app.include_router(alignment_router)
    app.include_router(graphrag_router)
    app.include_router(knowledge_router)
    app.include_router(resolution_router)
    app.include_router(risk_router)
    app.include_router(ontology_router)

    @app.exception_handler(GraphRagError)
    async def graphrag_error(request, exc: GraphRagError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.exception_handler(AlignmentError)
    async def alignment_error(request, exc: AlignmentError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.exception_handler(IntakeError)
    async def intake_error(request, exc: IntakeError):
        return JSONResponse(
            {"detail": exc.message, "location": exc.location}, status_code=exc.status
        )

    @app.exception_handler(pymysql.MySQLError)
    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, exc):
        return JSONResponse(
            {"detail": "暂存服务未完成操作，请稍后重试或检查存储空间"}, status_code=503
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc: RequestValidationError):
        # Never return request bodies: a rejected connection payload can contain a password.
        return JSONResponse(
            {
                "detail": "请求参数不合法，请检查输入",
                "fields": [".".join(map(str, e["loc"])) for e in exc.errors()],
            },
            status_code=422,
        )

    return app
