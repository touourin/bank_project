"""Reviewable resolution contracts; source graph records remain opaque and lossless."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResolutionOptions(RequestModel):
    method: Literal["evidence_v1", "synonym_llm_v1"] = "evidence_v1"
    model_policy: Literal["review", "apply"] = "review"
    max_model_calls: int = Field(default=2000, ge=1, le=20000)
    max_alias_calls: int = Field(default=200, ge=0, le=20000)
    concurrency: int = Field(default=8, ge=1, le=32)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    retrieval_policy: Literal["balanced", "original"] = "balanced"
    candidate_limit: int = Field(default=10, ge=1, le=500)
    max_pairs: int = Field(default=20000, ge=1, le=100000)
    max_block_size: int = Field(default=500, ge=1, le=5000)
    max_cluster_size: int = Field(default=100, ge=1, le=100)
    embedding_neighbors: int = Field(default=10, ge=1, le=100)
    max_vector_records: int = Field(default=2000, ge=1, le=20000)
    unique_id_namespaces: list[str] = Field(default_factory=list)
    synonyms: dict[str, Any] | None = None
    vectors: dict[str, Any] | None = None
    gold: dict[str, Any] | None = None


class StartRequest(RequestModel):
    source_kind: Literal["graphrag", "database"]
    source_id: str = Field(min_length=1, max_length=200)
    options: ResolutionOptions = Field(default_factory=ResolutionOptions)


class DecisionRequest(RequestModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    action: Literal["merge", "reject", "reset"]
    expected_revision: int = Field(ge=0)
    canonical_id: str | None = Field(default=None, min_length=1, max_length=200)
    reviewer: str = Field(default="人工校验", min_length=1, max_length=200)
    note: str = Field(default="", max_length=4000)


class ManualRequest(RequestModel):
    node_ids: list[str] = Field(min_length=2, max_length=100)
    expected_revision: int = Field(ge=0)
    canonical_id: str | None = Field(default=None, min_length=1, max_length=200)
    reviewer: str = Field(default="人工校验", min_length=1, max_length=200)
    note: str = Field(default="", max_length=4000)


class NodeBrief(BaseModel):
    id: str
    name: str
    type: str = ""


class Conflict(BaseModel):
    field: str
    values: list[dict[str, Any]]


class Candidate(BaseModel):
    id: str
    node_ids: list[str]
    nodes: list[NodeBrief]
    score: float
    reasons: list[str]
    evidence: dict[str, Any] = Field(default_factory=dict)
    conflicts: list[Conflict] = Field(default_factory=list)
    status: Literal["pending", "merged", "rejected", "excluded", "not_recommended"] = "pending"
    canonical_id: str | None = None
    decision_order: int = 0


class Audit(BaseModel):
    id: str
    action: Literal["merge", "reject", "reset", "manual"]
    candidate_id: str
    canonical_id: str | None = None
    reviewer: str
    note: str
    created_at: str
    revision: int
    previous_status: str
    source_nodes: list[NodeBrief] = Field(default_factory=list)


class Merge(BaseModel):
    candidate_id: str
    source_nodes: list[NodeBrief]
    target_node: NodeBrief


class Summary(BaseModel):
    original_node_count: int = 0
    original_edge_count: int = 0
    node_count: int = 0
    edge_count: int = 0
    pending_count: int = 0
    merged_count: int = 0
    rejected_count: int = 0
    excluded_count: int = 0
    not_recommended_count: int = 0


class ResolutionRun(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def preserve_historical_strategy(cls, value):
        # Old persisted runs predate strategy selection; do not relabel them.
        if isinstance(value, dict) and isinstance(value.get("options"), dict):
            if "retrieval_policy" not in value["options"]:
                value = {**value, "options": {**value["options"], "retrieval_policy": "original"}}
        return value

    id: str
    name: str
    source_kind: Literal["graphrag", "database"]
    source_id: str
    revision: int = 0
    status: Literal["analyzing", "ready", "failed"] = "analyzing"
    created_at: str
    progress: str = "正在完整读取来源图谱"
    error: str | None = None
    summary: Summary = Field(default_factory=Summary)
    candidates: list[Candidate] = Field(default_factory=list)
    audits: list[Audit] = Field(default_factory=list)
    merges: list[Merge] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    options: ResolutionOptions = Field(default_factory=ResolutionOptions)
