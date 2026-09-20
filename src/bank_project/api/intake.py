"""HTTP boundary for step 1. Authentication and request validation stay here."""

from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from bank_project.api.security import authorize
from bank_project.intake.models import (
    BatchDetail,
    BatchPage,
    IntakeError,
    IntakeJob,
    Limits,
    TablePage,
)
from bank_project.intake.mysql import CatalogTable, MysqlImport, MysqlRequest
from bank_project.intake.service import IntakeService


def service(request: Request) -> IntakeService:
    return request.app.state.intake


Service = Annotated[IntakeService, Depends(service)]
router = APIRouter(
    prefix="/api/v1/intake", tags=["第一步 · 数据接入"], dependencies=[Depends(authorize)]
)


@router.get("/limits", response_model=Limits)
def limits(intake: Service):
    return intake.limits


@router.post(
    "/uploads",
    response_model=BatchDetail | IntakeJob,
    status_code=201,
    responses={202: {"model": IntakeJob, "description": "文件已保存，等待后台解析"}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload(
    request: Request,
    response: Response,
    intake: Service,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    header_row: Annotated[int, Query(ge=1, le=100)] = 1,
    encoding: Literal["auto", "utf-8-sig", "gb18030"] = "auto",
    delimiter: Literal["auto", "comma", "tab", "semicolon"] = "auto",
    skip_description_sheets: bool = True,
):
    if "/" in filename or "\\" in filename or any(ord(c) < 32 for c in filename):
        raise IntakeError("请使用不含路径的文件名")
    jobs = request.app.state.intake_jobs
    if jobs is not None:
        if Path(filename).suffix.lower() not in {".xlsx", ".csv"}:
            raise IntakeError("目前支持 .xlsx 和 .csv 文件")
        from bank_project.intake.uploads import receive_upload

        job = await receive_upload(
            request,
            filename,
            {
                "header_row": header_row,
                "encoding": encoding,
                "delimiter": delimiter,
                "skip_description_sheets": skip_description_sheets,
            },
            intake.limits.max_upload_bytes,
        )
        response.status_code = 202
        return job
    content = await request.body()
    return await run_in_threadpool(
        intake.upload,
        content,
        filename,
        header_row=header_row,
        encoding=encoding,
        delimiter=delimiter,
        skip_description_sheets=skip_description_sheets,
    )


@router.post("/mysql/catalog", response_model=list[CatalogTable])
def mysql_catalog(payload: MysqlRequest, intake: Service):
    return intake.mysql.catalog(intake.connection(payload.connection))


@router.post(
    "/mysql/import",
    response_model=BatchDetail | IntakeJob,
    status_code=201,
    responses={202: {"model": IntakeJob}},
)
def mysql_import(payload: MysqlImport, intake: Service, request: Request, response: Response):
    jobs = request.app.state.intake_jobs
    if jobs is not None:
        from bank_project.staging.secrets import encrypt_connection

        connection = intake.connection(payload.connection)
        data = connection.model_dump()
        data["password"] = connection.password.get_secret_value()
        encrypted = encrypt_connection(
            request.app.state.settings.data_dir, {"connection": data, "tables": payload.tables}
        )
        response.status_code = 202
        return jobs.create(connection.database, "", {"mysql": encrypted}, "", 0)
    return intake.import_mysql(payload)


@router.get("/batches", response_model=BatchPage)
def batches(
    intake: Service,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return intake.store.list(offset, limit)


@router.get("/batches/{batch_id}", response_model=BatchDetail)
def batch(batch_id: UUID, intake: Service):
    return intake.store.get(str(batch_id))


@router.get("/batches/{batch_id}/tables/{table_id}", response_model=TablePage)
def table(
    batch_id: UUID,
    table_id: UUID,
    intake: Service,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return intake.store.preview(str(batch_id), str(table_id), offset, limit)


@router.delete("/batches/{batch_id}", status_code=204)
def delete(batch_id: UUID, intake: Service):
    intake.store.delete(str(batch_id))
    return Response(status_code=204)


@router.get("/jobs", response_model=list[IntakeJob])
def intake_jobs(request: Request):
    jobs = request.app.state.intake_jobs
    return jobs.list() if jobs else []


@router.get("/jobs/{job_id}", response_model=IntakeJob)
def intake_job(job_id: UUID, request: Request):
    jobs = request.app.state.intake_jobs
    if jobs is None:
        raise IntakeError("当前存储未启用后台接入", status=404)
    return jobs.get(str(job_id))


@router.post("/jobs/{job_id}/retry", response_model=IntakeJob)
def retry_intake(job_id: UUID, request: Request):
    jobs = request.app.state.intake_jobs
    if jobs is None:
        raise IntakeError("当前存储未启用后台接入", status=404)
    return jobs.retry(str(job_id))


@router.post("/jobs/{job_id}/cancel", response_model=IntakeJob)
def cancel_intake(job_id: UUID, request: Request):
    jobs = request.app.state.intake_jobs
    if jobs is None:
        raise IntakeError("当前存储未启用后台接入", status=404)
    return jobs.cancel(str(job_id))
