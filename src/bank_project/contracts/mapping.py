"""Declarative row-to-candidate mappings, independent of any bank or source table."""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from bank_project.contracts.models import Identifier, Model


class ColumnRule(Model):
    name: str
    sql_type: str
    format: Literal["YYYYMMDD", "json_object"] | None = None
    source_nullable: bool | None = None

    @model_validator(mode="after")
    def valid_type(self):
        kind = self.sql_type.lower()
        if kind in {"int", "string", "text"} or re.fullmatch(r"varchar\([1-9][0-9]{0,5}\)", kind):
            return self
        decimal = re.fullmatch(r"decimal\(([0-9]+),([0-9]+)\)", kind)
        if decimal:
            precision, scale = map(int, decimal.groups())
            if 1 <= precision <= 65 and 0 <= scale <= precision:
                return self
        raise ValueError(
            "Unsupported column type; use varchar(N), int, decimal(P,S), string or text"
        )


class EntityRule(Model):
    alias: Identifier
    entity_type: Identifier
    # Named external keys -> source field paths. Dotted paths traverse JSON objects.
    keys: dict[str, str] = Field(default_factory=dict)
    properties: dict[str, str] = Field(default_factory=dict)
    copy_row: bool = False
    identity_scope: Literal["record", "key"] = "record"
    optional: bool = False
    when: dict[str, str] = Field(default_factory=dict)


class ParticipantRule(Model):
    entity: Identifier
    role: Identifier
    optional: bool = False


class EventRule(Model):
    event_type: str | None = None
    type_field: str | None = None
    date_field: str | None = None
    attributes_field: str | None = None
    class_field: str | None = None
    participants: list[ParticipantRule] = Field(min_length=1)
    dictionary: dict = Field(default_factory=dict)
    pending_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def type_source(self):
        if bool(self.event_type) == bool(self.type_field):
            raise ValueError("Specify exactly one of event_type and type_field")
        return self


class RelationRule(Model):
    subject: Identifier
    predicate: Identifier
    object: Identifier
    optional: bool = False
    link_event: bool = False


class MappingProfile(Model):
    id: Identifier
    version: str = "1"
    table: str | None = None
    match_columns: list[str] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    columns: list[ColumnRule] = Field(default_factory=list)
    entities: list[EntityRule] = Field(min_length=1)
    event: EventRule | None = None
    relations: list[RelationRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self):
        if len({c.name for c in self.columns}) != len(self.columns):
            raise ValueError("Column rules must have unique names")
        aliases = {entity.alias for entity in self.entities}
        if len(aliases) != len(self.entities):
            raise ValueError("Entity aliases must be unique")
        for entity in self.entities:
            if entity.identity_scope == "key" and not entity.keys:
                raise ValueError("Key-scoped entities need external keys")
        if self.event and any(p.entity not in aliases for p in self.event.participants):
            raise ValueError("Event participant references an unknown entity alias")
        if any(r.subject not in aliases or r.object not in aliases for r in self.relations):
            raise ValueError("Relation references an unknown entity alias")
        if not self.event and any(r.link_event for r in self.relations):
            raise ValueError("A linked relation needs an event mapping")
        return self


@dataclass(frozen=True)
class MappingCatalog:
    profiles: tuple[MappingProfile, ...]
