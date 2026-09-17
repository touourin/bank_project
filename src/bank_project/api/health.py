from fastapi import APIRouter, HTTPException

from bank_project import __version__
from bank_project.api.dependencies import Services
from bank_project.api.models import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> HealthResponse:
    return HealthResponse(version=__version__)


@router.get("/ready", responses={503: {"description": "基础设施不可用或检查超时"}})
async def ready(services: Services) -> ReadyResponse:
    if services.storage is not None and not await services.storage.ready():
        raise HTTPException(503, "Storage unavailable")
    return ReadyResponse(graph_backend=services.graph_backend)
