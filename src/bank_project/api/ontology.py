"""Authenticated, read-only browsing of the shared ontology source."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from bank_project.api.security import authorize
from bank_project.ontology.browsing import topology
from bank_project.ontology.service import OntologyService


def service(request: Request) -> OntologyService:
    return request.app.state.ontology


Service = Annotated[OntologyService, Depends(service)]
router = APIRouter(
    prefix="/api/v1/ontology", tags=["共享 BFO 本体"], dependencies=[Depends(authorize)]
)


@router.get("/graph")
def graph(ontology: Service):
    catalog = ontology.current()
    return {"source": ontology.info(catalog), "graph": topology(catalog)}


@router.get("/concepts/{node_id}/dimensions")
def dimensions(
    node_id: str,
    ontology: Service,
    revision: str = Query(min_length=1, max_length=200),
    snapshot_sha256: str = Query(pattern=r"^[0-9a-f]{64}$"),
):
    return ontology.dimensions(revision, snapshot_sha256, node_id)
