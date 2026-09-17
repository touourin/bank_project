"""HTTP response models; transport details stay outside the business contracts."""

from typing import Literal

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    error: str
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    mode: Literal["framework"] = "framework"


class ReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    mode: Literal["framework"] = "framework"
    graph_backend: str


UNIMPLEMENTED_RESPONSE = {
    501: {"model": ErrorResponse, "description": "接口占位，业务功能尚未实现"}
}
