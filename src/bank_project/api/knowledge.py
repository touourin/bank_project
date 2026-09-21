"""Graph source selection and additive ontology matching HTTP boundary."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, model_validator

from bank_project.alignment.models import StrictModel
from bank_project.api.security import authorize
from bank_project.knowledge.service import KnowledgeService


def service(request: Request):
    return request.app.state.knowledge


Service = Annotated[KnowledgeService, Depends(service)]
router = APIRouter(
    prefix="/api/v1/knowledge",
    tags=["图谱来源与 GraphRAG 本体匹配"],
    dependencies=[Depends(authorize)],
)


class MatchRequest(StrictModel):
    source_kind: Literal["graphrag"]
    source_id: str = Field(min_length=1, max_length=200)


class MatchDecision(StrictModel):
    target: Literal["node", "edge"]
    target_id: str = Field(min_length=1, max_length=300)
    boid: str | None = Field(default=None, min_length=1, max_length=200)
    edge_type: str | None = Field(default=None, min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    reviewer: str = Field(default="人工审核", min_length=1, max_length=100)
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def explicit_value(self):
        required = "boid" if self.target == "node" else "edge_type"
        other = "edge_type" if self.target == "node" else "boid"
        if required not in self.model_fields_set or other in self.model_fields_set:
            raise ValueError("必须明确指定当前目标的挂载字段，可传 null 清除")
        return self


class AcceptMatchProposals(StrictModel):
    expected_revision: int = Field(ge=1)
    reviewer: str = Field(default="人工审核", min_length=1, max_length=100)
    note: str = Field(default="", max_length=2000)


@router.get("/sources")
def sources(knowledge: Service):
    return knowledge.sources()


@router.get("/matches")
def matches(knowledge: Service):
    return knowledge.store.list()


@router.post("/matches", status_code=202)
async def match(payload: MatchRequest, knowledge: Service):
    return await knowledge.start_match(payload.source_kind, payload.source_id)


@router.get("/matches/{run_id}")
def get_match(run_id: UUID, knowledge: Service):
    return knowledge.store.public(knowledge.store.get(str(run_id)))


@router.get("/matches/{run_id}/concepts")
def concepts(run_id: UUID, knowledge: Service, q: str = Query(default="", max_length=200)):
    return knowledge.concepts(str(run_id), q)


@router.post("/matches/{run_id}/decisions")
def decide(run_id: UUID, payload: MatchDecision, knowledge: Service):
    return knowledge.review(str(run_id), payload)


@router.post("/matches/{run_id}/accept-proposals")
def accept_proposals(run_id: UUID, payload: AcceptMatchProposals, knowledge: Service):
    return knowledge.accept_proposals(str(run_id), payload)


@router.get("/matches/{run_id}/graph")
def graph(run_id: UUID, knowledge: Service):
    return knowledge.result_graph(str(run_id))
