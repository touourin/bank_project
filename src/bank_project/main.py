from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from bank_project import __version__
from bank_project.api import graph, health, intake, pipeline
from bank_project.api.models import ErrorResponse
from bank_project.api.security import RequestGuardMiddleware, authorize
from bank_project.application import ApplicationServices
from bank_project.bootstrap import build_services
from bank_project.contracts.errors import FeatureNotImplemented, IntakeError
from bank_project.settings import Settings


def create_app(
    settings: Settings | None = None,
    services: ApplicationServices | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configuration = settings or Settings()
        active_services = services if services is not None else build_services(configuration)
        app.state.services = active_services
        app.state.api_token = (
            configuration.api_token.get_secret_value() if configuration.api_token else None
        )
        app.state.max_request_bytes = configuration.max_file_bytes + 1024 * 1024
        app.state.max_file_bytes = configuration.max_file_bytes
        app.state.max_concurrent_jobs = configuration.max_concurrent_jobs
        try:
            yield
        finally:
            if active_services.storage is not None:
                await active_services.storage.close()

    app = FastAPI(
        title="Bank project · 数据准备服务",
        version=__version__,
        lifespan=lifespan,
        description=(
            "通用文件/表格导入与对象、事件、关系转换；两步独立调用。"
            "业务表采用可配置映射，文档采用可配置模型。结果为带来源证据的候选数据。"
            "消歧、建图、语义推理和图谱查询仍为占位，返回 HTTP 501。"
        ),
    )
    app.add_middleware(RequestGuardMiddleware, max_bytes=101 * 1024 * 1024)

    @app.exception_handler(IntakeError)
    async def handle_intake_error(request: Request, exc: IntakeError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error=type(exc).__name__,
                detail=str(exc),
            ).model_dump(),
        )

    @app.exception_handler(FeatureNotImplemented)
    async def handle_unimplemented(request: Request, exc: FeatureNotImplemented) -> JSONResponse:
        return JSONResponse(
            status_code=501,
            content=ErrorResponse(error="FeatureNotImplemented", detail=str(exc)).model_dump(),
        )

    app.include_router(health.router)
    for router in (intake.router, pipeline.router, graph.router):
        app.include_router(router, dependencies=[Depends(authorize)])
    return app
