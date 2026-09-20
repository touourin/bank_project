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
from bank_project.api.health import router as health_router
from bank_project.api.intake import router as intake_router
from bank_project.intake.models import IntakeError
from bank_project.intake.mysql import MysqlConnection, MysqlSource
from bank_project.intake.service import IntakeService
from bank_project.intake.store import BatchStore
from bank_project.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings if settings is not None else Settings()
        config = app.state.settings
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
        )
        try:
            yield
        finally:
            await app.state.alignment.close()

    app = FastAPI(
        title="Bank project",
        version=__version__,
        description="第一步：通用数据接入与暂存。第二步：按表进行本体对齐、展示映射及生成独立图谱版本。",
        lifespan=lifespan,
    )
    app.add_middleware(BodyLimitMiddleware)
    app.include_router(health_router)
    app.include_router(intake_router)
    app.include_router(alignment_router)

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
