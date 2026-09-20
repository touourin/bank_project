"""Authenticated HTTP boundary for step 2."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from bank_project.alignment.models import (
    AnalyzeRequest,
    ConceptDetail,
    GraphNode,
    GraphOverview,
    GraphPage,
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
    result = alignment.store.get(str(run_id))
    if result.result and any(not t.source_name for t in result.result.tables):
        names = {s.table.id: s.batch.name for s in alignment.store.sources(str(run_id))}
        for table in result.result.tables:
            table.source_name = table.source_name or names.get(table.table_id, "")
    return result


@router.get("/runs/{run_id}/concepts", response_model=list[ConceptDetail])
def concepts(run_id: UUID, alignment: Service, q: str = Query(default="", max_length=200)):
    return alignment.concepts(str(run_id), q)


@router.post("/runs/{run_id}/mapping", response_model=Run, status_code=201)
def edit_mapping(run_id: UUID, payload: MappingEditRequest, alignment: Service):
    return alignment.edit(str(run_id), payload)


@router.post("/runs/{run_id}/graph", response_model=Run, status_code=202)
async def generate(run_id: UUID, alignment: Service, payload: GraphTemplate | None = None):
    return await alignment.generate(str(run_id), payload)


@router.get("/graph", response_model=GraphPreview)
def graph(alignment: Service):
    return alignment.graph.current()


@router.get("/graph/overview", response_model=GraphOverview)
def graph_overview(alignment: Service):
    return alignment.graph.browser.overview()


@router.get("/graph/{version}/nodes", response_model=GraphPage)
def graph_page(
    version: UUID,
    alignment: Service,
    concept: str = Query(default="", max_length=200),
    q: str = Query(default="", max_length=200),
    after: str = Query(default="", max_length=200),
    limit: int = Query(default=100, ge=1, le=200),
    focus: str = Query(default="", max_length=200),
):
    return alignment.graph.browser.page(
        str(version), concept=concept, query=q, after=after, limit=limit, focus=focus
    )


@router.get("/graph/{version}/nodes/{node_id}", response_model=GraphNode)
def graph_detail(version: UUID, node_id: str, alignment: Service):
    if not node_id or len(node_id) > 200:
        from bank_project.alignment.models import AlignmentError

        raise AlignmentError("实例标识无效")
    return alignment.graph.browser.detail(str(version), node_id)


@router.post("/runs/{run_id}/template", response_model=Run, status_code=201)
def save_template(run_id: UUID, payload: GraphTemplate, alignment: Service):
    return alignment.save_template(str(run_id), payload)


@router.post("/runs/{run_id}/template/default", response_model=GraphTemplate)
def default_template(run_id: UUID, payload: GraphTemplate, alignment: Service):
    return alignment.default_template(str(run_id), payload)
