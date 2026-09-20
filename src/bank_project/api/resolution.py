"""Authenticated resolution review and full derivative graph export."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from bank_project.api.security import authorize
from bank_project.resolution.models import (
    DecisionRequest,
    ManualRequest,
    ResolutionRun,
    StartRequest,
)
from bank_project.resolution.service import ResolutionService


def service(request: Request) -> ResolutionService:
    return request.app.state.resolution


Service = Annotated[ResolutionService, Depends(service)]
router = APIRouter(
    prefix="/api/v1/resolution", tags=["实体消歧与人工校验"], dependencies=[Depends(authorize)]
)


@router.post("/runs", response_model=ResolutionRun, status_code=202)
async def create(payload: StartRequest, resolution: Service):
    return await resolution.start(payload)


@router.get("/runs", response_model=list[ResolutionRun])
def runs(resolution: Service):
    return resolution.list()


@router.get("/runs/{run_id}", response_model=ResolutionRun)
def run(run_id: UUID, resolution: Service):
    return resolution.get(str(run_id))


@router.post("/runs/{run_id}/decisions", response_model=ResolutionRun)
def decide(run_id: UUID, payload: DecisionRequest, resolution: Service):
    return resolution.decide(str(run_id), payload)


@router.post("/runs/{run_id}/manual", response_model=ResolutionRun)
def manual(run_id: UUID, payload: ManualRequest, resolution: Service):
    return resolution.manual(str(run_id), payload)


@router.get("/runs/{run_id}/graph")
def graph(run_id: UUID, resolution: Service):
    return resolution.graph(str(run_id))


@router.get("/runs/{run_id}/original")
def original(run_id: UUID, resolution: Service):
    return resolution.store.original(str(run_id))
