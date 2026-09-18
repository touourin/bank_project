"""Map one source record with a declarative profile; no I/O or cross-row merging."""

import json
from dataclasses import dataclass
from datetime import date

from bank_project.contracts.errors import IntakeError
from bank_project.contracts.intake import SourceRow, StoredImport
from bank_project.contracts.mapping import MappingProfile
from bank_project.contracts.models import (
    EntityCandidate,
    EventCandidate,
    Evidence,
    ExternalKey,
    Participant,
    RelationCandidate,
    SourceRecord,
)
from bank_project.extraction.identity import identity
from bank_project.extraction.validation import column_value, event_properties


def field(values: dict, path: str):
    # Exact names win, so source column names containing dots remain addressable.
    if path in values:
        return values[path]
    current = values
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


@dataclass(frozen=True)
class MappedRow:
    source: SourceRecord
    evidence: Evidence
    entities: list[EntityCandidate]
    event: EventCandidate | None
    relations: list[RelationCandidate]
    warnings: list[str]


class RowMapper:
    def __init__(self, profile: MappingProfile, source: StoredImport):
        self.profile, self.source = profile, source
        self.columns = {c.name: c.model_dump() for c in profile.columns}
        self.namespace = (
            source.request.dataset_id,
            source.request.source_system,
            profile.id,
            profile.table or source.parsed.table,
        )

    def convert(self, row: SourceRow) -> MappedRow:
        values = self._values(row)
        key = self._record_key(row, values)
        request = self.source.request
        sid = identity(
            "source",
            request.dataset_id,
            request.batch_id,
            self.source.receipt.artifact.digest,
            row.locator,
        )
        eid = identity("evidence", sid)
        entities, aliases = self._entities(values, sid, eid, row.locator)
        event, warnings = self._event(values, key, aliases, eid, row.locator)
        if self.columns and row.values.keys() - self.columns.keys():
            warnings.append("存在字段契约之外的列，已原样保留在来源证据中")
        return MappedRow(
            source=SourceRecord(
                source_id=sid,
                source_system=request.source_system,
                record_key=key,
                locator=f"{self.source.parsed.filename}#{row.locator}",
                artifact=self.source.receipt.artifact,
            ),
            evidence=Evidence(evidence_id=eid, source_id=sid, fields=row.values),
            entities=entities,
            event=event,
            relations=self._relations(aliases, key, eid, event, row.locator),
            warnings=warnings,
        )

    def _values(self, row: SourceRow) -> dict:
        if not set(self.profile.match_columns).issubset(row.values):
            raise IntakeError(f"{row.locator}: 缺少映射要求的字段")
        values = dict(row.values)
        for name, column in self.columns.items():
            if name not in values:
                if column.get("source_nullable") is False:
                    raise IntakeError(f"{row.locator}: 缺少已明确非空的字段 {name}")
                continue
            try:
                values[name] = column_value(values[name], column)
            except IntakeError as exc:
                raise IntakeError(f"{row.locator} 字段 {name}: {exc}") from exc
        return values

    def _record_key(self, row: SourceRow, values: dict) -> str:
        if self.profile.primary_key:
            keys = [field(values, name) for name in self.profile.primary_key]
            if any(value is None or not str(value).strip() for value in keys):
                raise IntakeError(f"{row.locator}: 主键不能为空")
        else:
            keys = [self.source.receipt.artifact.digest, row.locator]
        return json.dumps(keys, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def _entities(
        self, values: dict, sid: str, eid: str, locator: str
    ) -> tuple[list[EntityCandidate], dict[str, str]]:
        result, aliases = [], {}
        request = self.source.request
        for rule in self.profile.entities:
            if any(field(values, path) != expected for path, expected in rule.when.items()):
                continue
            keys = {name: field(values, path) for name, path in rule.keys.items()}
            if any(value is None or value == "" for value in keys.values()):
                if rule.optional:
                    continue
                raise IntakeError(f"{locator}: 对象 {rule.alias} 缺少外部标识")
            if any(not isinstance(value, str) or not value.strip() for value in keys.values()):
                raise IntakeError(f"{locator}: 外部标识必须为非空文本，不能将账号等转为数字")
            candidate_id = identity(
                "entity",
                request.dataset_id,
                request.source_system,
                rule.entity_type,
                keys if rule.identity_scope == "key" else [self.profile.id, sid, rule.alias],
            )
            aliases[rule.alias] = candidate_id
            properties = dict(values) if rule.copy_row else {}
            properties.update({name: field(values, path) for name, path in rule.properties.items()})
            result.append(
                EntityCandidate(
                    candidate_id=candidate_id,
                    entity_type=rule.entity_type,
                    external_keys=[
                        ExternalKey(source_system=request.source_system, key_type=name, value=value)
                        for name, value in keys.items()
                    ],
                    properties=properties,
                    evidence_ids=[eid],
                )
            )
        return result, aliases

    def _event(
        self, values: dict, key: str, aliases: dict, eid: str, locator: str
    ) -> tuple[EventCandidate | None, list[str]]:
        rule = self.profile.event
        if rule is None:
            return None, []
        code = rule.event_type or field(values, rule.type_field)
        if not isinstance(code, str) or not code.strip():
            raise IntakeError(f"{locator}: 事件类型不能为空")
        definition = rule.dictionary.get(
            code, {"name": code, "definition": "undefined", "properties": {}}
        )
        if (
            rule.class_field
            and definition.get("event_class")
            and field(values, rule.class_field) != definition["event_class"]
        ):
            raise IntakeError(f"{locator}: 事件大类与映射字典不一致")
        attributes = field(values, rule.attributes_field) if rule.attributes_field else {}
        if not isinstance(attributes, dict):
            raise IntakeError(f"{locator}: 事件属性必须为对象，请配置 json_object 字段格式")
        try:
            attributes = event_properties(attributes, definition.get("properties", {}))
            occurred = field(values, rule.date_field) if rule.date_field else None
            occurred_on = date.fromisoformat(occurred) if occurred else None
        except IntakeError as exc:
            raise IntakeError(f"{locator}: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise IntakeError(f"{locator}: 事件日期无效") from exc
        participants = []
        for participant in rule.participants:
            if participant.entity not in aliases:
                if participant.optional:
                    continue
                raise IntakeError(f"{locator}: 事件缺少必需的参与方")
            participants.append(
                Participant(entity_id=aliases[participant.entity], role=participant.role)
            )
        pending = bool(rule.dictionary) and (
            code in rule.pending_codes
            or definition.get("definition")
            in {
                "undefined",
                "temporary_mock_code",
                "user_confirmed_placeholder",
            }
        )
        warnings = [f"{code}: 保留原码和属性，业务定义或属性口径待确认"] if pending else []
        return EventCandidate(
            event_id=identity("event", *self.namespace, key),
            event_type=code,
            occurred_on=occurred_on,
            participants=participants,
            evidence_ids=[eid],
            properties={
                "source_fields": values,
                "attributes": attributes,
                "event_name": definition["name"],
                "requires_confirmation": pending,
            },
        ), warnings

    def _relations(
        self, aliases: dict, key: str, eid: str, event: EventCandidate | None, locator: str
    ) -> list[RelationCandidate]:
        result = []
        for index, rule in enumerate(self.profile.relations):
            if rule.subject not in aliases or rule.object not in aliases:
                if rule.optional:
                    continue
                raise IntakeError(f"{locator}: 关系缺少必需的端点对象")
            result.append(
                RelationCandidate(
                    relation_id=identity("relation", *self.namespace, key, index),
                    subject_id=aliases[rule.subject],
                    predicate=rule.predicate,
                    object_id=aliases[rule.object],
                    event_id=event.event_id if rule.link_event and event else None,
                    evidence_ids=[eid],
                )
            )
        return result
