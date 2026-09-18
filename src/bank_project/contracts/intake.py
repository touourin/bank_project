"""Contracts for the two independently callable preparation stages."""

from datetime import datetime
from typing import Literal

from pydantic import Field, JsonValue

from bank_project.contracts.models import (
    ExtractionBatch,
    Identifier,
    ImportRequest,
    Model,
    SourceArtifact,
)


class RawInput(Model):
    filename: str
    content: bytes


class SourceRow(Model):
    locator: str
    values: dict[str, JsonValue]


class TextBlock(Model):
    locator: str
    text: str


class ParsedSource(Model):
    filename: str
    kind: Literal["table", "document"] = "document"
    table: str | None = None
    rows: list[SourceRow] = Field(default_factory=list)
    blocks: list[TextBlock] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class IngestionReceipt(Model):
    dataset_id: Identifier
    batch_id: Identifier
    source_system: Identifier
    artifact: SourceArtifact
    filename: str
    table: str | None
    records: int
    imported_at: datetime
    warnings: list[str] = Field(default_factory=list)


class StoredImport(Model):
    request: ImportRequest
    receipt: IngestionReceipt
    parsed: ParsedSource


class ExtractionRequest(Model):
    dataset_id: Identifier
    batch_id: Identifier
    mapping: Identifier | None = None


class ExtractionSummary(Model):
    dataset_id: Identifier
    batch_id: Identifier
    status: Literal["completed"] = "completed"
    entities: int
    events: int
    relations: int
    evidence: int
    input_digest: str
    configuration_digest: str
    producer_version: str
    mapping: str | None = None
    warnings: list[str] = Field(default_factory=list)
    completed_at: datetime


class ExtractionState(Model):
    dataset_id: Identifier
    batch_id: Identifier
    status: Literal["not_started", "running", "failed", "completed"]
    detail: str | None = None
    summary: ExtractionSummary | None = None


class StoredExtraction(Model):
    summary: ExtractionSummary
    batch: ExtractionBatch


class MappingSummary(Model):
    id: str
    version: str
    table: str | None
    match_columns: list[str]
