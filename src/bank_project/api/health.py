"""Liveness is local; readiness checks only the required internal staging service."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from bank_project import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str = __version__


class ReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"


@router.get("/health")
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/ready")
def ready(request: Request) -> ReadyResponse:
    jobs = request.app.state.intake_jobs
    if jobs is not None:
        with jobs.database.connect() as db:
            db.execute("SELECT 1")
    return ReadyResponse()
