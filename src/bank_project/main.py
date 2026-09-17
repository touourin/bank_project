from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from bank_project import __version__
from bank_project.api import graph, health, pipeline
from bank_project.api.models import ErrorResponse
from bank_project.application import ApplicationServices
from bank_project.bootstrap import build_services
from bank_project.contracts.errors import FeatureNotImplemented
from bank_project.settings import Settings


def create_app(
    settings: Settings | None = None,
    services: ApplicationServices | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active_services = (
            services if services is not None else build_services(settings or Settings())
        )
        app.state.services = active_services
        try:
            yield
        finally:
            if active_services.storage is not None:
                await active_services.storage.close()

    app = FastAPI(
        title="Bank project · 工程框架",
        version=__version__,
        lifespan=lifespan,
        description=(
            "仅提供工程结构、接口契约与基础设施连接。"
            "业务接口为占位，合法请求返回 HTTP 501；数据导入、抽取、消歧、建图、查询均未实现。"
        ),
    )

    @app.exception_handler(FeatureNotImplemented)
    async def handle_unimplemented(request: Request, exc: FeatureNotImplemented) -> JSONResponse:
        return JSONResponse(
            status_code=501,
            content=ErrorResponse(error="FeatureNotImplemented", detail=str(exc)).model_dump(),
        )

    for router in (health.router, pipeline.router, graph.router):
        app.include_router(router)
    return app
