"""Human mapping revisions preserve model evidence and source data."""

from datetime import UTC, datetime

from .catalog import Catalog
from .models import (
    AlignmentError,
    ConceptRef,
    MappingEdit,
    MappingEditRequest,
    MappingResult,
    RelationProposal,
    SourceTable,
)
from .relations import map_relations
from .templates import TemplateNode, TemplateProperty


def revise_mapping(
    original: MappingResult,
    request: MappingEditRequest,
    catalog: Catalog,
    sources: list[SourceTable],
    index=None,
) -> MappingResult:
    result = original.model_copy(deep=True)
    if result.template:
        result.template.confirmed = False
    table = next((t for t in result.tables if t.table_id == request.table_id), None)
    if table is None:
        raise AlignmentError("所选表不属于该分析任务")
    if not table.row_count:
        raise AlignmentError("空表没有可生成的实例，请先导入数据")
    after = catalog.describe(request.concept_id) if request.concept_id else None
    target = table
    if request.column is not None:
        target = next((c for c in table.columns if c.column == request.column), None)
        if target is None:
            raise AlignmentError("所选字段不属于该表")
    before = (
        ConceptRef(id=target.concept_id, name=target.concept_name or target.concept_id)
        if target.concept_id
        else None
    )
    target.concept_id = after.id if after else None
    target.concept_name = after.name if after else None
    if request.column is None:
        table.status, table.verification = "mapped", "manual"
        if result.template:
            if not any(n.table_id == table.table_id for n in result.template.nodes):
                result.template.nodes.append(
                    TemplateNode(
                        id=table.table_id,
                        table_id=table.table_id,
                        concept_id=after.id,
                        concept_name=after.name,
                        identity_scope=table.table_id,
                        properties=[
                            TemplateProperty(column=c.column, name=c.column)
                            for c in table.columns
                            if c.role != "ignore"
                        ],
                    )
                )
            for node in result.template.nodes:
                if node.id == table.table_id:
                    node.concept_id, node.concept_name = after.id, after.name
            result.template.note = "映射已修改，请重新核对实体分组及字段归属并确认图模板。"
    table.manual_edits.append(
        MappingEdit(
            created_at=datetime.now(UTC).isoformat(),
            column=request.column,
            before=before,
            after=after,
            reason=request.reason.strip(),
        )
    )
    # Recheck availability and exact joins; manual concept selection cannot invent edges.
    proposals: dict[str, list[RelationProposal]] = {}
    for relation in result.relations:
        if relation.origin == "candidate":
            proposals.setdefault(relation.source_table_id, []).append(
                RelationProposal(
                    target_table_id=relation.target_table_id,
                    source_columns=relation.source_columns,
                    target_columns=relation.target_columns,
                    reason=relation.reason,
                )
            )
    result.relations = map_relations(sources, result.tables, proposals, index)
    return result
