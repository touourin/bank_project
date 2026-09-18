import hashlib
from dataclasses import dataclass

from bank_project.contracts.errors import LimitExceeded
from bank_project.contracts.intake import StoredImport
from bank_project.contracts.models import (
    EntityCandidate,
    EventCandidate,
    Evidence,
    ExtractionBatch,
    Participant,
    RelationCandidate,
    SourceRecord,
)
from bank_project.extraction.identity import identity
from bank_project.ports import DocumentCache, DocumentModel


@dataclass(frozen=True)
class Chunk:
    text: str
    start: int
    locators: tuple[str, ...]


def chunks(
    source: StoredImport, size: int = 4000, overlap: int = 200, maximum: int = 64
) -> list[Chunk]:
    spans = []
    pieces = []
    offset = 0
    for block in source.parsed.blocks:
        spans.append((offset, offset + len(block.text), block.locator))
        pieces.append(block.text)
        offset += len(block.text) + 2
    text = "\n\n".join(pieces)
    result = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        result.append(
            Chunk(
                text=text[start:end],
                start=start,
                locators=tuple(
                    locator for left, right, locator in spans if right > start and left < end
                ),
            )
        )
        if len(result) > maximum:
            raise LimitExceeded("文档切块数超限，请拆分；本批次尚未调用模型")
        if end == len(text):
            break
        start = end - overlap
    return result


class DocumentExtractor:
    def __init__(self, model: DocumentModel, cache: DocumentCache):
        self.model, self.cache = model, cache
        self.configuration_digest = hashlib.sha256(
            f"chunks:4000:200:64;{model.fingerprint}".encode()
        ).hexdigest()

    def extract(self, source: StoredImport, producer: str) -> tuple[ExtractionBatch, list[str]]:
        request = source.request
        batch = ExtractionBatch(
            dataset_id=request.dataset_id, batch_id=request.batch_id, producer_version=producer
        )
        for chunk in chunks(source):
            sid = identity(
                "source",
                request.dataset_id,
                request.batch_id,
                source.receipt.artifact.digest,
                chunk.start,
            )
            key = hashlib.sha256(
                f"{sid}:{self.configuration_digest}:{chunk.text}".encode()
            ).hexdigest()
            output = self.cache.get(key)
            if output is None:
                output = self.model.extract(chunk.text)
                output.validate_evidence(chunk.text)
                self.cache.put(key, output)
            else:
                output.validate_evidence(chunk.text)
            batch.sources.append(
                SourceRecord(
                    source_id=sid,
                    source_system=request.source_system,
                    record_key=f"chunk:{chunk.start}",
                    locator=f"{source.parsed.filename}#{','.join(chunk.locators)};text_chars:{chunk.start}-{chunk.start + len(chunk.text)}",
                    artifact=source.receipt.artifact,
                )
            )
            entity_ids = {e.id: identity("entity", sid, e.id) for e in output.entities}
            event_ids = {e.id: identity("event", sid, e.id) for e in output.events}
            evidence_ids = {}
            for item in [*output.entities, *output.events, *output.relations]:
                if item.quote in evidence_ids:
                    continue
                eid = identity("evidence", sid, item.quote)
                evidence_ids[item.quote] = eid
                start = chunk.start + chunk.text.index(item.quote)
                batch.evidence.append(
                    Evidence(
                        evidence_id=eid,
                        source_id=sid,
                        excerpt=item.quote,
                        fields={
                            "text_char_start": start,
                            "text_char_end": start + len(item.quote),
                            "source_blocks": list(chunk.locators),
                            "review_required": True,
                        },
                    )
                )
            for entity in output.entities:
                batch.entities.append(
                    EntityCandidate(
                        candidate_id=entity_ids[entity.id],
                        entity_type=entity.type,
                        properties={
                            "name": entity.name,
                            "attributes": entity.properties,
                            "review_required": True,
                        },
                        evidence_ids=[evidence_ids[entity.quote]],
                    )
                )
            for event in output.events:
                batch.events.append(
                    EventCandidate(
                        event_id=event_ids[event.id],
                        event_type=event.type,
                        occurred_on=event.occurred_on,
                        participants=[
                            Participant(entity_id=entity_ids[p.entity_id], role=p.role)
                            for p in event.participants
                        ],
                        properties={"attributes": event.properties, "review_required": True},
                        evidence_ids=[evidence_ids[event.quote]],
                    )
                )
            for index, relation in enumerate(output.relations):
                batch.relations.append(
                    RelationCandidate(
                        relation_id=identity("relation", sid, index),
                        subject_id=entity_ids[relation.subject_id],
                        predicate=relation.predicate,
                        object_id=entity_ids[relation.object_id],
                        event_id=event_ids[relation.event_id] if relation.event_id else None,
                        evidence_ids=[evidence_ids[relation.quote]],
                    )
                )
        return batch, [
            *source.parsed.warnings,
            "文档输出为待复核候选；跨块重复与同名实体交给后续消歧，不自动合并",
        ]
