"""Deterministic editing presets; never infer business identities or invent ontology nodes."""

from .templates import GraphTemplate, TemplateNode, TemplateProperty


def row_record_template(sources, mappings, current: GraphTemplate, catalog) -> GraphTemplate:
    """Keep every source row and field, using the existing whole-table object choice."""
    by_table = {m.table_id: m for m in mappings}
    nodes, excluded = [], []
    for source in sources:
        if not source.table.row_count:
            continue
        mapping = by_table[source.table.id]
        existing = [n for n in current.nodes if n.table_id == source.table.id]
        if len(existing) == 1 and existing[0].concept_id in catalog.names:
            # Retain a user's object-type edit while resetting only the generation rules.
            choice = existing[0].model_copy(deep=True)
        elif (
            mapping.concept_id in catalog.names
            and mapping.status in {"mapped", "review"}
            and mapping.verification not in {"mismatch", "unavailable"}
        ):
            choice = TemplateNode(
                id=source.table.id,
                table_id=source.table.id,
                concept_id=mapping.concept_id,
                identity_scope=source.table.id,
                retrieval_target="table",
                retrieval_name=source.table.name,
            )
        else:
            excluded.append(f"{source.batch.name} / {source.table.name}")
            continue
        choice.id = choice.table_id = source.table.id
        choice.concept_name = catalog.names[choice.concept_id]
        choice.identity_scope = f"row:{source.table.id}"
        choice.key_columns = []
        choice.properties = [
            TemplateProperty(column=c.name, name=c.name) for c in source.table.columns
        ]
        nodes.append(choice)
    return GraphTemplate(
        mode="row_records",
        nodes=nodes,
        note=("缺少整表对象候选，默认方案未包含：" + "、".join(excluded))[:2000]
        if excluded
        else "",
    )
