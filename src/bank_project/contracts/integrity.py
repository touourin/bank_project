from bank_project.contracts.errors import IntakeError
from bank_project.contracts.models import ExtractionBatch


def validate_batch(batch: ExtractionBatch) -> None:
    groups = [
        [item.source_id for item in batch.sources],
        [item.evidence_id for item in batch.evidence],
        [item.candidate_id for item in batch.entities],
        [item.event_id for item in batch.events],
        [item.relation_id for item in batch.relations],
    ]
    if any(len(items) != len(set(items)) for items in groups):
        raise IntakeError("转换结果存在重复标识")
    sources, evidence, entities, events, _ = map(set, groups)
    if any(item.source_id not in sources for item in batch.evidence):
        raise IntakeError("证据引用了不存在的来源")
    for item in [*batch.entities, *batch.events, *batch.relations]:
        if not item.evidence_ids or not set(item.evidence_ids).issubset(evidence):
            raise IntakeError("候选结果缺少有效来源证据")
    for event in batch.events:
        if not event.participants or any(p.entity_id not in entities for p in event.participants):
            raise IntakeError("事件缺少有效参与方")
    for relation in batch.relations:
        if relation.subject_id not in entities or relation.object_id not in entities:
            raise IntakeError("关系端点不存在")
        if relation.event_id is not None and relation.event_id not in events:
            raise IntakeError("关系关联的事件不存在")
