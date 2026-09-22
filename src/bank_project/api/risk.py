"""Authenticated HTTP boundary for ontology-derived risk RulePacks."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from bank_project.api.security import authorize
from bank_project.risk.models import ExecutionRequest, PropagationRequest, ReviewRequest
from bank_project.risk.service import RiskService


def service(request: Request) -> RiskService:
    return request.app.state.risk


Service = Annotated[RiskService, Depends(service)]
router = APIRouter(
    prefix="/api/v1/risk", tags=["WHY 风险规则与人工审核"], dependencies=[Depends(authorize)]
)


@router.get("/catalog")
def catalog(
    risk: Service,
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
):
    return risk.catalog(q, limit)


@router.get("/sources")
def sources(risk: Service):
    return risk.sources()


@router.post("/propagations", status_code=202)
async def propagate(payload: PropagationRequest, risk: Service):
    return await risk.start(payload)


@router.get("/propagations")
def propagations(risk: Service):
    return [{k: v for k, v in row.items() if k != "anchors"} for row in risk.store.list("jobs")]


@router.get("/propagations/{job_id}")
def propagation(job_id: UUID, risk: Service):
    return risk.store.get("jobs", str(job_id))


@router.get("/cases")
def cases(risk: Service):
    return risk.store.list("cases")


@router.get("/cases/{case_id}")
def case(case_id: UUID, risk: Service):
    return risk.store.get("cases", str(case_id))


@router.post("/cases/{case_id}/review")
def review(case_id: UUID, payload: ReviewRequest, risk: Service):
    return risk.review(str(case_id), payload)


@router.get("/cases/{case_id}/fields")
def fields(case_id: UUID, graph_version: UUID, risk: Service):
    return risk.fields(str(case_id), str(graph_version))


@router.post("/cases/{case_id}/executions")
async def execute(case_id: UUID, payload: ExecutionRequest, risk: Service):
    return await risk.execute(str(case_id), payload)


@router.get("/cases/{case_id}/executions")
def executions(case_id: UUID, risk: Service):
    risk.store.get("cases", str(case_id))
    return risk.store.list("executions", str(case_id))
