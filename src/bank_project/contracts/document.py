"""Model output is untrusted: validate shape, references and verbatim evidence."""

from datetime import date

from pydantic import Field, JsonValue

from bank_project.contracts.errors import IntakeError
from bank_project.contracts.models import Identifier, Model, Participant


class DocumentEntity(Model):
    id: Identifier
    type: Identifier
    name: str = Field(min_length=1, max_length=400)
    quote: str = Field(min_length=1, max_length=8000)
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class DocumentEvent(Model):
    id: Identifier
    type: Identifier
    occurred_on: date | None = None
    participants: list[Participant] = Field(min_length=1, max_length=50)
    quote: str = Field(min_length=1, max_length=8000)
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class DocumentRelation(Model):
    subject_id: Identifier
    predicate: Identifier
    object_id: Identifier
    event_id: Identifier | None = None
    quote: str = Field(min_length=1, max_length=8000)


class DocumentExtraction(Model):
    entities: list[DocumentEntity] = Field(max_length=100)
    events: list[DocumentEvent] = Field(max_length=100)
    relations: list[DocumentRelation] = Field(max_length=200)

    def validate_evidence(self, text: str) -> None:
        entity_ids = {item.id for item in self.entities}
        event_ids = {item.id for item in self.events}
        if len(entity_ids) != len(self.entities) or len(event_ids) != len(self.events):
            raise IntakeError("模型结果包含重复的局部标识")
        for item in [*self.entities, *self.events, *self.relations]:
            if not item.quote.strip() or item.quote not in text:
                raise IntakeError("模型返回的证据不是当前文本中的原文")
        for entity in self.entities:
            if entity.name not in entity.quote:
                raise IntakeError("模型实体名称未出现在其原文证据中")
        for event in self.events:
            if any(p.entity_id not in entity_ids for p in event.participants):
                raise IntakeError("模型事件引用了不存在的参与方")
        for relation in self.relations:
            if relation.subject_id not in entity_ids or relation.object_id not in entity_ids:
                raise IntakeError("模型关系引用了不存在的实体")
            if relation.event_id is not None and relation.event_id not in event_ids:
                raise IntakeError("模型关系引用了不存在的事件")
