"""Explicit contracts shared by analysis, persistence and graph adapters."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bank_project.conversion.audit import ReviewStamp
from bank_project.intake.models import BatchInfo, DataRow, TableInfo

from .templates import EdgeSuggestion, EntitySuggestion, GraphTemplate


class AlignmentError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message, self.status = message, status


class IncompleteModelOutput(AlignmentError):
    """The provider reached its output limit; interpretation may retry smaller batches."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Selection(StrictModel):
    batch_id: UUID
    table_id: UUID


class AnalyzeRequest(StrictModel):
    tables: list[Selection] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def unique_tables(self):
        if len({t.table_id for t in self.tables}) != len(self.tables):
            raise ValueError("不能重复选择同一张表")
        return self


class SourceTable(BaseModel):
    batch: BatchInfo
    table: TableInfo
    rows: list[DataRow]
    staged: bool = False


class ColumnProposal(StrictModel):
    column: str = Field(max_length=512)
    role: Literal["primary_key", "foreign_key", "attribute", "ignore"] = "attribute"
    semantic: str = Field(default="", max_length=80)
    concept_id: str | None = Field(default=None, max_length=200)
    reason: str = Field(default="", max_length=500)


class RelationProposal(StrictModel):
    target_table_id: str = Field(max_length=100)
    source_columns: list[str] = Field(min_length=1, max_length=16)
    target_columns: list[str] = Field(min_length=1, max_length=16)
    reason: str = Field(max_length=500)


class TableProposal(StrictModel):
    entities: list[EntitySuggestion] = Field(default_factory=list, max_length=12)
    edges: list[EdgeSuggestion] = Field(default_factory=list, max_length=24)
    concept_id: str | None = Field(max_length=200)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason: str = Field(max_length=2000)
    columns: list[ColumnProposal] = Field(max_length=2048)
    relations: list[RelationProposal] = Field(default_factory=list, max_length=30)


class TableMeaning(StrictModel):
    meaning: str = Field(max_length=1000)
    search_terms: list[str] = Field(min_length=1, max_length=8)
    attribute_terms: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def bounded_terms(self):
        if any(
            not term.strip() or len(term) > 80 for term in self.search_terms + self.attribute_terms
        ):
            raise ValueError("检索词过长或为空")
        return self


class ColumnMapping(ColumnProposal):
    property_key: str
    concept_name: str | None = None


class ConceptRef(BaseModel):
    id: str
    name: str


class ConceptDetail(ConceptRef):
    parents: list[ConceptRef] = Field(default_factory=list)
    semantic_type: str | None = None
    has_why: bool = False


class ScoredConcept(ConceptDetail):
    score: float | None = None


class RetrievalTrace(BaseModel):
    target: Literal["table", "column", "entity"]
    name: str
    query: str
    status: Literal["matched", "review", "unmatched", "unavailable", "mismatch"]
    candidates: list[ScoredConcept] = Field(default_factory=list)
    selected: ScoredConcept | None = None
    confident: bool = False
    match_method: str = "none"
    detail: str = ""


class MatchStep(BaseModel):
    key: Literal["meaning", "recall", "selection", "validation"]
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    detail: str = ""


class MatchTrace(BaseModel):
    method: Literal["model", "retrieve"] = "model"
    retrievals: list[RetrievalTrace] = Field(default_factory=list)
    steps: list[MatchStep] = Field(
        default_factory=lambda: [
            MatchStep(key=key) for key in ("meaning", "recall", "selection", "validation")
        ]
    )
    sample_rows: list[int] = Field(default_factory=list)
    meaning: TableMeaning | None = None
    candidates: list[ConceptDetail] = Field(default_factory=list)
    attribute_candidates: list[ConceptDetail] = Field(default_factory=list)
    selected: ConceptDetail | None = None
    selection_reason: str = ""
    selection_confidence: float | None = None
    verification: str = "none"
    selection_attempts: int = 0
    confidence_threshold: float


class MappingEditRequest(StrictModel):
    table_id: str = Field(min_length=1, max_length=100)
    column: str | None = Field(default=None, max_length=512)
    concept_id: str | None = Field(max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    reviewer: str = Field(default="人工审核", min_length=1, max_length=100)

    @model_validator(mode="after")
    def meaningful_edit(self):
        if not self.reason.strip():
            raise ValueError("请填写修改依据")
        if self.column is None and not self.concept_id:
            raise ValueError("整表映射需要选择本体概念")
        return self


class MappingEdit(ReviewStamp):
    version: str | None = None
    column: str | None = None
    before: ConceptRef | None = None
    after: ConceptDetail | None = None
    reason: str


class TableMapping(BaseModel):
    table_id: str
    batch_id: str
    table_name: str
    source_name: str = ""
    row_count: int
    concept_id: str | None = None
    concept_name: str | None = None
    confidence: float = 0
    status: Literal["mapped", "review", "unmatched", "failed"] = "unmatched"
    verification: Literal[
        "local_catalog", "verified", "mismatch", "unavailable", "none", "manual"
    ] = "none"
    reason: str = ""
    columns: list[ColumnMapping] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trace: MatchTrace | None = None
    structure_notes: list[str] = Field(default_factory=list)
    manual_edits: list[MappingEdit] = Field(default_factory=list)


class RelationMapping(BaseModel):
    id: str
    source_table_id: str
    target_table_id: str
    source_columns: list[str]
    target_columns: list[str]
    origin: Literal["declared", "candidate"]
    reason: str
    status: Literal["ready", "skipped"]
    matched_rows: int = 0
    warnings: list[str] = Field(default_factory=list)


class MappingResult(BaseModel):
    template: GraphTemplate | None = None
    revision: str
    snapshot_sha256: str
    ontology_id: str | None = None
    tables: list[TableMapping]
    relations: list[RelationMapping]
    warnings: list[str] = Field(default_factory=list)


class Run(BaseModel):
    id: str
    created_at: str
    status: Literal["analyzing", "ready", "failed"] = "analyzing"
    progress: str = "等待分析"
    error: str | None = None
    result: MappingResult | None = None
    graph_status: Literal["none", "building", "ready", "failed"] = "none"
    graph_error: str | None = None
    graph_version: str | None = None
    based_on_run_id: str | None = None


class GraphSummary(BaseModel):
    version: str
    run_id: str
    revision: str
    ontology_id: str | None = None
    node_count: int
    edge_count: int
    created_at: str


class GraphNodeBrief(BaseModel):
    id: str
    name: str
    table_id: str
    table_name: str
    source_row: int
    concept_id: str
    concept_name: str


class GraphNode(GraphNodeBrief):
    fields: dict[str, str | None]


class GraphEdge(BaseModel):
    source: str
    target: str
    relation_id: str
    origin: Literal["declared", "candidate", "confirmed"]
    name: str = ""


class GraphPreview(BaseModel):
    summary: GraphSummary | None = None
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class GraphGroup(BaseModel):
    concept_id: str
    concept_name: str
    count: int


class GraphOverview(BaseModel):
    summary: GraphSummary | None = None
    groups: list[GraphGroup] = Field(default_factory=list)


class GraphPage(BaseModel):
    nodes: list[GraphNodeBrief] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    total: int = 0
    next_cursor: str | None = None
    anchor: GraphNodeBrief | None = None
    edges_truncated: bool = False
