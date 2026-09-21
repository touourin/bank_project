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


@router.get("/runs/{run_id}/candidates/{candidate_id}/sources")
def candidate_sources(run_id: UUID, candidate_id: str, resolution: Service):
    return resolution.store.candidate_sources(str(run_id), candidate_id)


@router.get("/experiments")
def experiments(resolution: Service):
    from bank_project.resolution.experiments import list_experiments

    runs, errors = list_experiments(resolution.experiments_root)
    return {"runs": runs, "errors": errors}


def _experiment(resolution, name):
    from bank_project.alignment.models import AlignmentError
    from bank_project.resolution.experiments import load_experiment

    try:
        return load_experiment(resolution.experiments_root, name)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise AlignmentError("实验不完整或产物校验失败，无法读取", 409) from exc


@router.get("/experiments/{name}")
def experiment(name: str, resolution: Service):
    import json

    from bank_project.resolution.experiments import record_rows

    value = _experiment(resolution, name)
    return {
        "name": value.name,
        "manifest": value.manifest,
        "report": value.report,
        "corpus": value.corpus,
        "results": value.results,
        "differences": value.differences,
        "gold": value.gold,
        "config": value.config,
        "records": record_rows(value),
        "artifacts": list(value.artifacts),
        "aliases": json.loads(value.artifacts.get("alias_expansion.json", b"null")),
        "synonyms": json.loads(value.artifacts.get("synonyms.proposals.json", b"null")),
    }


@router.get("/experiments/{name}/download")
def experiment_download(name: str, resolution: Service):
    import io
    import json
    import zipfile

    from starlette.responses import Response

    value = _experiment(resolution, name)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("run.json", json.dumps(value.manifest, ensure_ascii=False, indent=2))
        for path, raw in value.artifacts.items():
            archive.writestr(path, raw)
    return Response(
        output.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="entity-resolution.zip"'},
    )
