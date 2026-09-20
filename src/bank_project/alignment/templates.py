"""Reviewable graph templates: field ownership, identity scopes and evidenced joins."""

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TemplateProperty(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)


class TemplateNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_:.-]+$")
    table_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_:.-]+$")
    concept_id: str = Field(min_length=1, max_length=200)
    concept_name: str = ""
    identity_scope: str = Field(min_length=1, max_length=200)
    key_columns: list[str] = Field(default_factory=list, max_length=16)
    properties: list[TemplateProperty] = Field(default_factory=list, max_length=2048)


class TemplateEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_:.-]+$")
    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    mode: Literal["same_row", "join"] = "same_row"
    source_columns: list[str] = Field(default_factory=list, max_length=16)
    target_columns: list[str] = Field(default_factory=list, max_length=16)
    reason: str = Field(min_length=1, max_length=1000)


class EntitySuggestion(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    concept_id: str = Field(min_length=1, max_length=200)
    key_columns: list[str] = Field(default_factory=list, max_length=16)
    properties: list[TemplateProperty] = Field(default_factory=list, max_length=2048)


class EdgeSuggestion(BaseModel):
    source: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)


class GraphTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: list[TemplateNode] = Field(default_factory=list, max_length=120)
    edges: list[TemplateEdge] = Field(default_factory=list, max_length=240)
    confirmed: bool = False
    note: str = Field(default="", max_length=2000)


def draft_template(sources, mappings, relations, suggestions=None, catalog=None):
    """Conservative initial template. Shared identity requires explicit user confirmation."""
    nodes = []
    for source, mapping in zip(sources, mappings, strict=True):
        if mapping.status != "mapped" or not mapping.concept_id:
            continue
        nodes.append(
            TemplateNode(
                id=source.table.id,
                table_id=source.table.id,
                concept_id=mapping.concept_id,
                concept_name=mapping.concept_name or "",
                identity_scope=source.table.id,
                key_columns=[c.name for c in source.table.columns if c.primary_key],
                properties=[
                    TemplateProperty(column=c.column, name=c.column)
                    for c in mapping.columns
                    if c.role != "ignore"
                ],
            )
        )
    from .models import AlignmentError

    suggested_edges = []
    rejected = []
    for source in sources:
        proposal = (suggestions or {}).get(source.table.id)
        # A single-object table already has a stable, source-keyed default above.
        # Only multi-object suggestions require a split template for review.
        if not proposal or len(proposal.entities) < 2 or catalog is None:
            continue
        local = {e.id: str(uuid4()) for e in proposal.entities}
        if len(local) != len(proposal.entities):
            rejected.append(source.table.name)
            continue
        candidates = [
            TemplateNode(
                id=local[e.id],
                table_id=source.table.id,
                concept_id=e.concept_id,
                concept_name=catalog.names.get(e.concept_id, ""),
                identity_scope=source.table.id + ":" + e.id,
                key_columns=e.key_columns,
                properties=e.properties,
            )
            for e in proposal.entities
        ]
        edges = [
            TemplateEdge(
                id=str(uuid4()),
                source=local[e.source],
                target=local[e.target],
                name=e.name,
                reason=e.reason,
            )
            for e in proposal.edges
            if e.source in local and e.target in local
        ]
        try:
            validate_template(GraphTemplate(nodes=candidates, edges=edges), [source], catalog)
        except (ValueError, AlignmentError):
            rejected.append(source.table.name)
            continue
        if (
            len([n for n in nodes if n.table_id != source.table.id]) + len(candidates) > 120
            or len(suggested_edges) + len(edges) > 200
        ):
            rejected.append(source.table.name)
            continue
        nodes = [n for n in nodes if n.table_id != source.table.id] + candidates
        suggested_edges.extend(edges)
    ids = {n.id for n in nodes}
    edges = [
        TemplateEdge(
            id=r.id,
            source=r.source_table_id,
            target=r.target_table_id,
            name="关联",
            mode="join",
            source_columns=r.source_columns,
            target_columns=r.target_columns,
            reason=r.reason or "来源关联字段",
        )
        for r in relations
        if r.status == "ready" and r.source_table_id in ids and r.target_table_id in ids
    ]
    return GraphTemplate(
        nodes=nodes,
        edges=edges + suggested_edges,
        note=(
            "部分实体分组建议未通过字段或概念校验，已保留按表草稿，请人工调整："
            + "、".join(rejected)
        )[:2000]
        if rejected
        else "",
    )


def validate_template(template, sources, catalog):
    from .models import AlignmentError

    result = template.model_copy(deep=True)
    tables = {s.table.id: s.table for s in sources}
    nodes = {n.id: n for n in result.nodes}
    if not nodes or len(nodes) != len(result.nodes):
        raise AlignmentError("模板节点不能为空或重名")
    if len({e.id for e in result.edges}) != len(result.edges):
        raise AlignmentError("模板关系标识不能重复")
    for n in result.nodes:
        table = tables.get(n.table_id)
        if table is None or n.concept_id not in catalog.names:
            raise AlignmentError("模板引用了本次数据或本体版本以外的表或概念")
        n.concept_name = catalog.names[n.concept_id]
        names = {c.name for c in table.columns}
        if len(set(n.key_columns)) != len(n.key_columns) or not set(n.key_columns) <= names:
            raise AlignmentError("节点身份字段重复或不存在")
        if (
            len({p.name for p in n.properties}) != len(n.properties)
            or not {p.column for p in n.properties} <= names
        ):
            raise AlignmentError("节点属性名称重复或源字段不存在")
        if not n.identity_scope.strip():
            raise AlignmentError("请填写身份范围")
    # A scope is a business identity contract: key arity and concept must agree.
    scopes = {}
    for n in result.nodes:
        identity = (n.concept_id, len(n.key_columns))
        if n.identity_scope in scopes and scopes[n.identity_scope] != identity:
            raise AlignmentError("同一身份范围必须使用同一概念及相同数量的标识字段")
        scopes[n.identity_scope] = identity
    for edge in result.edges:
        if edge.source not in nodes or edge.target not in nodes:
            raise AlignmentError("关系端点不存在")
        a, b = nodes[edge.source], nodes[edge.target]
        if edge.mode == "same_row":
            if a.table_id != b.table_id:
                raise AlignmentError("同一行关系的两端必须来自同一张表")
        else:
            if not edge.source_columns or len(edge.source_columns) != len(edge.target_columns):
                raise AlignmentError("跨表关系需要成对的连接字段")
            for node, cols in ((a, edge.source_columns), (b, edge.target_columns)):
                if len(set(cols)) != len(cols) or not set(cols) <= {
                    c.name for c in tables[node.table_id].columns
                }:
                    raise AlignmentError("关系连接字段重复或不存在")
    result.confirmed = True
    return result
