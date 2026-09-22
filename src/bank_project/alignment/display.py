"""Human-readable labels only; display choices never change instance identity."""

import re

from .models import GraphNode


def display_name(fields, concept, row, fallback="") -> str:
    values = {re.sub(r"[\s_-]", "", k).lower(): v for k, v in fields.items() if v}
    for key in (
        "客户名称",
        "客户姓名",
        "企业名称",
        "公司名称",
        "名称",
        "姓名",
        "custnm",
        "custname",
        "customername",
        "companyname",
        "name",
        "title",
    ):
        if values.get(key):
            return values[key][:200]
    event = (
        values.get("事件名称")
        or values.get("eventname")
        or values.get("事件类型")
        or values.get("evttype")
    )
    if event:
        date = values.get("occurdt") or values.get("发生日期")
        return (f"{event} · {date}" if date else event)[:200]
    return (fallback or f"{concept or '记录'} · 第 {row} 行")[:200]


def readable_node(node: GraphNode) -> GraphNode:
    fallback = node.name if node.name not in {node.table_id, node.id} else ""
    return node.model_copy(
        update={"name": display_name(node.fields, node.concept_name, node.source_row, fallback)}
    )
