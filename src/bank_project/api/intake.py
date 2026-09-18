from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, UploadFile

from bank_project.api.dependencies import Services
from bank_project.api.models import ErrorResponse
from bank_project.contracts.errors import DependencyUnavailable, LimitExceeded
from bank_project.contracts.intake import (
    ExtractionRequest,
    ExtractionState,
    ExtractionSummary,
    IngestionReceipt,
    MappingSummary,
    RawInput,
)
from bank_project.contracts.models import ExtractionBatch, Identifier, ImportRequest

router = APIRouter(
    prefix="/api/v1",
    tags=["preparation · 数据导入与转换"],
    responses={code: {"model": ErrorResponse} for code in (404, 409, 413, 422, 503)},
)


@router.get("/mappings", response_model=list[MappingSummary], summary="列出可用的表格映射配置")
def list_mappings(services: Services) -> list[MappingSummary]:
    if services.extraction is None:
        raise DependencyUnavailable("转换服务未装配")
    return services.extraction.mappings()


@router.post(
    "/imports", response_model=IngestionReceipt, summary="第一步：导入收件目录文件或只读 MySQL 表"
)
def import_source(data: ImportRequest, services: Services) -> IngestionReceipt:
    if services.ingestion is None:
        raise DependencyUnavailable("导入服务未装配")
    return services.ingestion.ingest(data)


@router.post(
    "/imports/upload", response_model=IngestionReceipt, summary="第一步：上传文件并保留原始数据"
)
def upload_source(
    request: Request,
    services: Services,
    dataset_id: Annotated[Identifier, Form()],
    batch_id: Annotated[Identifier, Form()],
    source_system: Annotated[Identifier, Form()],
    file: Annotated[UploadFile, File()],
    table: Annotated[Identifier | None, Form()] = None,
    sheet: Annotated[Identifier | None, Form()] = None,
) -> IngestionReceipt:
    if services.ingestion is None:
        raise DependencyUnavailable("导入服务未装配")
    # Run upload processing in the same bounded worker pool as other sync routes.
    try:
        maximum = request.app.state.max_file_bytes
        content = file.file.read(maximum + 1)
        if len(content) > maximum:
            raise LimitExceeded("文件大小超限")
        filename = file.filename or ""
        data = ImportRequest(
            dataset_id=dataset_id,
            batch_id=batch_id,
            source_system=source_system,
            source_uri=f"upload:{filename}",
            table=table,
            sheet=sheet,
        )
        return services.ingestion.ingest(data, RawInput(filename=filename, content=content))
    finally:
        file.file.close()


@router.get("/imports/{batch_id}", response_model=IngestionReceipt, summary="读取导入回执")
def get_import(
    batch_id: Identifier, dataset_id: Annotated[Identifier, Query()], services: Services
) -> IngestionReceipt:
    if services.ingestion is None:
        raise DependencyUnavailable("导入服务未装配")
    return services.ingestion.get(dataset_id, batch_id)


@router.post(
    "/extractions",
    response_model=ExtractionSummary,
    summary="第二步：按映射转换表格，或抽取文档；同步执行",
)
def extract_source(data: ExtractionRequest, services: Services) -> ExtractionSummary:
    if services.extraction is None:
        raise DependencyUnavailable("转换服务未装配")
    return services.extraction.extract(data)


@router.get(
    "/extractions/{batch_id}/status", response_model=ExtractionState, summary="转换状态及失败原因"
)
def extraction_status(
    batch_id: Identifier, dataset_id: Annotated[Identifier, Query()], services: Services
) -> ExtractionState:
    if services.extraction is None:
        raise DependencyUnavailable("转换服务未装配")
    return services.extraction.state(dataset_id, batch_id)


@router.get(
    "/extractions/{batch_id}", response_model=ExtractionBatch, summary="读取完整候选结果及来源证据"
)
def extraction_result(
    batch_id: Identifier, dataset_id: Annotated[Identifier, Query()], services: Services
) -> ExtractionBatch:
    if services.extraction is None:
        raise DependencyUnavailable("转换服务未装配")
    return services.extraction.result(dataset_id, batch_id)
