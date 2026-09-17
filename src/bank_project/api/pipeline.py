from typing import Annotated

from fastapi import APIRouter, Query

from bank_project.api.dependencies import Services
from bank_project.api.models import UNIMPLEMENTED_RESPONSE
from bank_project.contracts.models import ExtractionBatch, Identifier, ImportRequest, RunResult

router = APIRouter(
    prefix="/api/v1",
    tags=["pipeline · 尚未实现"],
    responses=UNIMPLEMENTED_RESPONSE,
)


@router.post("/imports", response_model=RunResult, summary="数据导入（尚未实现）")
def import_records(data: ImportRequest, services: Services) -> RunResult:
    return services.pipeline.import_data(data)


@router.post("/batches", response_model=RunResult, summary="批次处理（尚未实现）")
def submit_batch(batch: ExtractionBatch, services: Services) -> RunResult:
    return services.pipeline.run(batch)


@router.get("/runs/{run_id}", response_model=RunResult, summary="运行记录（尚未实现）")
def get_run(
    run_id: Identifier, dataset_id: Annotated[Identifier, Query()], services: Services
) -> RunResult:
    return services.pipeline.get_run(dataset_id, run_id)
