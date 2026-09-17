from typing import Annotated

from fastapi import APIRouter, Query

from bank_project.api.dependencies import Services
from bank_project.api.models import UNIMPLEMENTED_RESPONSE
from bank_project.contracts.models import EvidenceResult, GraphQuery, Identifier, QueryResult

router = APIRouter(
    prefix="/api/v1",
    tags=["graph · 尚未实现"],
    responses=UNIMPLEMENTED_RESPONSE,
)


@router.post("/graph/query", response_model=QueryResult, summary="图谱查询（尚未实现）")
def query_graph(request: GraphQuery, services: Services) -> QueryResult:
    return services.query.query(request)


@router.get(
    "/evidence/{evidence_id}", response_model=EvidenceResult, summary="证据查询（尚未实现）"
)
def evidence(
    evidence_id: Identifier, dataset_id: Annotated[Identifier, Query()], services: Services
) -> EvidenceResult:
    return services.query.evidence(dataset_id, evidence_id)
