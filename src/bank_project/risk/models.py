"""Explicit request contracts for generation, review and read-only execution."""

from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NodeId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
SourceField = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PropagationRequest(StrictModel):
    anchor_node_ids: list[NodeId] = Field(min_length=1, max_length=20)
    brief: str = Field(default="请基于 WHY 与本体传导路径生成可审核的风险规则", max_length=4000)
    dataset_revision: str | None = Field(default=None, min_length=1, max_length=128)
    max_depth: int = Field(default=5, ge=1, le=10)
    max_candidates: int = Field(default=50, ge=1, le=200)

    @field_validator("anchor_node_ids")
    @classmethod
    def unique_anchors(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("节点不能重复")
        return values


class VersionRequest(StrictModel):
    expected_version: int = Field(ge=1)
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewRequest(VersionRequest):
    action: Literal["approve", "reject"]
    evidence_confirmed: bool = False
    reason: str = Field(default="", max_length=2000)


class ExecutionRequest(VersionRequest):
    graph_version: UUID
    field_mapping: dict[NodeId, SourceField] = Field(min_length=1, max_length=12)
    start: str = Field(max_length=64)
    end: str = Field(max_length=64)

    @model_validator(mode="after")
    def bounded_window(self):
        start, end = (datetime.fromisoformat(v) for v in (self.start, self.end))
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("时间必须含时区")
        if not start < end or end - start > timedelta(days=366):
            raise ValueError("查询时间范围应为正且不超过 366 天")
        return self
