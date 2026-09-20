"""Authenticated HTTP boundary for step 2."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from bank_project.alignment.models import (
    AnalyzeRequest,
    ConceptDetail,
    GraphPreview,
    MappingEditRequest,
    Run,
)
from bank_project.alignment.service import AlignmentService
from bank_project.alignment.templates import GraphTemplate
from bank_project.api.security import authorize


def service(request: Request) -> AlignmentService:
    return request.app.state.alignment


Service = Annotated[AlignmentService, Depends(service)]
router = APIRouter(
    prefix="/api/v1/alignment",
    tags=["第二步 · 本体对齐与图谱生成"],
    dependencies=[Depends(authorize)],
)


@router.get("/config")
def config(alignment: Service):
    return alignment.config()


@router.post("/runs", response_model=Run, status_code=202)
async def analyze(payload: AnalyzeRequest, alignment: Service):
    return await alignment.analyze(payload.tables)


@router.get("/runs", response_model=list[Run])
def runs(alignment: Service):
    return [run.model_copy(update={"result": None}) for run in alignment.store.list()]


@router.get("/runs/{run_id}", response_model=Run)
def run(run_id: UUID, alignment: Service):
    return alignment.store.get(str(run_id))


@router.get("/runs/{run_id}/concepts", response_model=list[ConceptDetail])
def concepts(run_id: UUID, alignment: Service, q: str = Query(default="", max_length=200)):
    return alignment.concepts(str(run_id), q)


@router.post("/runs/{run_id}/mapping", response_model=Run, status_code=201)
def edit_mapping(run_id: UUID, payload: MappingEditRequest, alignment: Service):
    return alignment.edit(str(run_id), payload)


@router.post("/runs/{run_id}/graph", response_model=Run, status_code=202)
async def generate(run_id: UUID, alignment: Service):
    return await alignment.generate(str(run_id))


@router.get("/graph", response_model=GraphPreview)
def graph(alignment: Service):
    return alignment.graph.current()


@router.post("/runs/{run_id}/template", response_model=Run, status_code=201)
def save_template(run_id: UUID, payload: GraphTemplate, alignment: Service):
    return alignment.save_template(str(run_id), payload)
