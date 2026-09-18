"""Select a mapping and combine independently converted records into a batch."""

import hashlib

from bank_project.contracts.errors import IntakeError
from bank_project.contracts.intake import StoredImport
from bank_project.contracts.mapping import EntityRule, MappingCatalog, MappingProfile
from bank_project.contracts.models import EntityCandidate, ExtractionBatch
from bank_project.extraction.mapping_rows import RowMapper

GENERIC = MappingProfile(
    id="generic_record",
    entities=[EntityRule(alias="record", entity_type="record", copy_row=True)],
)


class EntityCollector:
    """Merge explicit identities while keeping ordered, unique evidence in linear time."""

    def __init__(self):
        self.entities: dict[str, EntityCandidate] = {}
        self.evidence: dict[str, set[str]] = {}

    def add(self, candidate: EntityCandidate, locator: str) -> None:
        key = candidate.candidate_id
        previous = self.entities.get(key)
        if previous is None:
            self.entities[key] = candidate
            self.evidence[key] = set(candidate.evidence_ids)
            return
        if any(
            name in previous.properties and previous.properties[name] != value
            for name, value in candidate.properties.items()
        ):
            raise IntakeError(
                f"{locator}: 同一对象标识出现冲突属性，请使用按记录隔离的映射或拆分快照"
            )
        previous.properties.update(candidate.properties)
        seen = self.evidence[key]
        for evidence_id in candidate.evidence_ids:
            if evidence_id not in seen:
                previous.evidence_ids.append(evidence_id)
                seen.add(evidence_id)


class StructuredExtractor:
    def __init__(self, catalog: MappingCatalog):
        self.catalog = catalog

    def select(self, source: StoredImport, mapping: str | None) -> MappingProfile:
        if mapping == GENERIC.id:
            return GENERIC
        if mapping is not None:
            candidates = [p for p in self.catalog.profiles if p.id == mapping]
        else:
            candidates = [p for p in self.catalog.profiles if p.table == source.parsed.table]
            if not candidates:
                headers = set(source.parsed.rows[0].values)
                candidates = [
                    p
                    for p in self.catalog.profiles
                    if p.match_columns and set(p.match_columns).issubset(headers)
                ]
            if not candidates:
                return GENERIC
        if len(candidates) != 1:
            raise IntakeError("映射配置不存在或匹配不唯一，请显式指定 mapping")
        return candidates[0]

    def digest(self, source: StoredImport, mapping: str | None) -> str:
        return hashlib.sha256(self.select(source, mapping).model_dump_json().encode()).hexdigest()

    def extract(
        self, source: StoredImport, producer: str, mapping: str | None = None
    ) -> tuple[ExtractionBatch, list[str]]:
        profile = self.select(source, mapping)
        mapper = RowMapper(profile, source)
        batch = ExtractionBatch(
            dataset_id=source.request.dataset_id,
            batch_id=source.request.batch_id,
            producer_version=producer,
        )
        entities = EntityCollector()
        warnings = set(source.parsed.warnings)
        if profile.id == GENERIC.id:
            warnings.add(
                "未使用业务映射：每行保留为独立 record 对象；配置 mapping 后可转换对象、事件和关系"
            )
        primary_keys = set()
        for row in source.parsed.rows:
            result = mapper.convert(row)
            if result.source.record_key in primary_keys:
                raise IntakeError(f"{row.locator}: 同一文件出现重复主键，不会合并或覆盖")
            primary_keys.add(result.source.record_key)
            batch.sources.append(result.source)
            batch.evidence.append(result.evidence)
            for entity in result.entities:
                entities.add(entity, row.locator)
            if result.event:
                batch.events.append(result.event)
            batch.relations.extend(result.relations)
            warnings.update(result.warnings)
        batch.entities = list(entities.entities.values())
        return batch, sorted(warnings)
