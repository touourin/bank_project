"""HTTP routes for TXT imports, durable GraphRAG indexing, graphs and questions."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from bank_project.api.security import authorize
from bank_project.graphrag import GraphRagError, GraphRagService
from bank_project.graphrag.parsers import MAX_FILE_BYTES, UploadValidationError


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=16000)
    method: Literal["local", "global", "drift"] = "local"


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except GraphRagError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    except UploadValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (ValueError, TypeError, OSError, KeyError, ImportError) as exc:
        raise HTTPException(503, "GraphRAG 数据或依赖不可用，请检查配置与存储") from exc


def build_router(settings=None) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/graphrag", tags=["GraphRAG 文档图谱"], dependencies=[Depends(authorize)]
    )
    supplied = GraphRagService(settings) if settings is not None else None

    def get_service(request: Request) -> GraphRagService:
        return supplied or request.app.state.graphrag

    @router.get("/config")
    def config(request: Request):
        return _call(get_service(request).config)

    @router.get("/datasets")
    def datasets(request: Request):
        return _call(get_service(request).list_datasets)

    @router.post("/uploads", status_code=201)
    async def upload(
        request: Request,
        filename: Annotated[str, Query(min_length=1, max_length=255)],
        name: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    ):
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_FILE_BYTES:
                raise HTTPException(413, "单个 TXT 文件不能超过 20 MB")
            content.extend(chunk)
        return await run_in_threadpool(
            _call, get_service(request).upload, bytes(content), filename, name
        )

    @router.get("/datasets/{key}")
    def dataset(key: str, request: Request):
        return _call(get_service(request).dataset, key)

    @router.post("/datasets/{key}/index", status_code=202)
    def index(key: str, request: Request):
        return _call(get_service(request).start_index, key)

    @router.get("/datasets/{key}/graph")
    def graph(key: str, request: Request):
        return _call(get_service(request).graph, key)

    @router.post("/datasets/{key}/query")
    async def query(key: str, payload: QueryRequest, request: Request):
        try:
            return await get_service(request).query(key, payload.question, payload.method)
        except GraphRagError as exc:
            raise HTTPException(exc.status, exc.message) from exc
        except (OSError, ValueError, KeyError, ImportError) as exc:
            raise HTTPException(503, "GraphRAG 索引或配置不可用，请检查服务后重试") from exc

    return router


router = build_router()
