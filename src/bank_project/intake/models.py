"""Source-neutral contracts. Cell values retain their textual precision."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IntakeError(Exception):
    def __init__(self, message: str, *, location: str = "", status: int = 422):
        super().__init__(message)
        self.message = message
        self.location = location
        self.status = status


class Limits(BaseModel):
    max_upload_bytes: int = 20 * 1024 * 1024
    max_expanded_bytes: int = 100 * 1024 * 1024
    max_tables: int = 30
    max_rows: int = 50_000
    max_columns: int = 512
    max_cells: int = 2_000_000
    max_cell_chars: int = 100_000


class IntakeJob(BaseModel):
    id: str
    created_at: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    name: str
    size_bytes: int
    rows_done: int
    attempt: int
    batch_id: str | None
    error: str | None


class Column(BaseModel):
    name: str
    data_type: str = "text"
    type_origin: Literal["observed", "declared"] = "observed"
    nullable: bool = True
    comment: str = ""
    primary_key: bool = False


class ForeignKey(BaseModel):
    name: str
    columns: list[str]
    target_schema: str
    target_table: str
    target_columns: list[str]


class TableInfo(BaseModel):
    id: str
    name: str
    row_count: int
    columns: list[Column]
    foreign_keys: list[ForeignKey] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BatchInfo(BaseModel):
    id: str
    name: str
    source_kind: Literal["file", "mysql"]
    source: str
    created_at: str
    table_count: int
    row_count: int
    sha256: str | None = None
    warnings: list[str] = Field(default_factory=list)


class BatchDetail(BatchInfo):
    tables: list[TableInfo]


class BatchPage(BaseModel):
    items: list[BatchInfo]
    total: int
    offset: int
    limit: int


class DataRow(BaseModel):
    number: int
    values: list[str | None]


class TablePage(TableInfo):
    rows: list[DataRow]
    offset: int
    limit: int


@dataclass
class ParsedTable:
    name: str
    columns: list[Column]
    rows: list[DataRow]
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    row_count: int = 0


@dataclass
class ParsedSource:
    tables: list[ParsedTable]
    warnings: list[str] = field(default_factory=list)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
