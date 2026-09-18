"""Handoff contracts for data preparation and reserved downstream graph stages."""

from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, StringConstraints

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ImportRequest(Model):
    dataset_id: Identifier
    batch_id: Identifier
    source_system: Identifier
    source_uri: str
    table: Identifier | None = None
    sheet: Identifier | None = None


class SourceArtifact(Model):
    uri: str
    digest: str


class SourceRecord(Model):
    source_id: Identifier
    source_system: Identifier
    record_key: str
    locator: str
    artifact: SourceArtifact | None = None


class Evidence(Model):
    evidence_id: Identifier
    source_id: Identifier
    fields: dict[str, JsonValue] = Field(default_factory=dict)
    excerpt: str | None = None


class ExternalKey(Model):
    source_system: Identifier
    key_type: Identifier
    value: str


class EntityCandidate(Model):
    candidate_id: Identifier
    entity_type: Identifier
    external_keys: list[ExternalKey] = Field(default_factory=list)
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_ids: list[Identifier] = Field(default_factory=list)


class Participant(Model):
    entity_id: Identifier
    role: Identifier


class EventCandidate(Model):
    event_id: Identifier
    event_type: Identifier
    occurred_at: AwareDatetime | None = None
    occurred_on: date | None = None
    participants: list[Participant] = Field(default_factory=list)
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_ids: list[Identifier] = Field(default_factory=list)


class RelationCandidate(Model):
    relation_id: Identifier
    subject_id: Identifier
    predicate: Identifier
    object_id: Identifier
    event_id: Identifier | None = None
    evidence_ids: list[Identifier] = Field(default_factory=list)


class ExtractionBatch(Model):
    schema_version: Literal["0.1", "0.2"] = "0.2"
    dataset_id: Identifier
    batch_id: Identifier
    producer_version: str
    sources: list[SourceRecord] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    entities: list[EntityCandidate] = Field(default_factory=list)
    events: list[EventCandidate] = Field(default_factory=list)
    relations: list[RelationCandidate] = Field(default_factory=list)


class ResolutionResult(Model):
    candidate_to_canonical: dict[str, str]
    resolver_version: str


class GraphNode(Model):
    node_id: Identifier
    node_type: Identifier
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_ids: list[Identifier] = Field(default_factory=list)


class GraphEdge(Model):
    edge_id: Identifier
    source_id: Identifier
    target_id: Identifier
    relation_type: Identifier
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_ids: list[Identifier] = Field(default_factory=list)


class GraphPatch(Model):
    dataset_id: Identifier
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    sources: list[SourceRecord] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class SemanticReport(Model):
    policy_version: str
    issues: list[str] = Field(default_factory=list)


class RunResult(Model):
    run_id: Identifier
    dataset_id: Identifier
    batch_id: Identifier
    status: Literal["pending", "running", "completed", "failed"]


class GraphQuery(Model):
    dataset_id: Identifier
    query_type: Identifier
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class QueryResult(Model):
    dataset_id: Identifier
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class EvidenceResult(Model):
    evidence: Evidence
    source: SourceRecord
