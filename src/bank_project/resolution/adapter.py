"""Adapt complete GraphRAG/DB snapshots without treating raw attributes as instructions."""

import json
from copy import deepcopy
from difflib import SequenceMatcher

from bank_project.alignment.models import AlignmentError

from .engine.candidates import normalize, terms
from .engine.contracts import Corpus, digest
from .models import Candidate, Conflict, NodeBrief

IDENTIFIER_FIELDS = {
    "cust_id",
    "cust_ind",
    "customer_id",
    "ecif_cust_id",
    "unify_credit_code",
    "unified_social_credit_code",
    "credit_code",
    "id_number",
    "account_number",
    "客户号",
    "统一社会信用代码",
    "身份证号",
    "账号",
}


def validate_graph(raw, kind, source_id):
    """Reject incomplete or ambiguous input; never operate on a preview silently."""
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("nodes"), list)
        or not isinstance(raw.get("edges"), list)
    ):
        raise AlignmentError("来源未返回完整的节点和边", 422)
    graph = deepcopy(raw)
    if not isinstance(graph.get("id"), str) or not graph["id"]:
        raise AlignmentError("来源图谱缺少稳定版本 ID", 422)
    if any(
        graph.get(key) for key in ("truncated", "edges_truncated", "nodes_truncated", "next_cursor")
    ):
        raise AlignmentError("来源返回的是截断图谱，不能用于消歧；请读取完整版本", 422)
    if graph.get("source_kind") != kind or graph.get("source_id") != source_id:
        raise AlignmentError("图谱来源或版本不一致，请刷新后重试", 409)
    ids = set()
    for node in graph["nodes"]:
        if (
            not isinstance(node, dict)
            or not isinstance(node.get("id"), str)
            or not node["id"]
            or node["id"] in ids
        ):
            raise AlignmentError("来源图谱包含缺失或重复的节点 ID", 422)
        if not isinstance(node.get("name"), str) or not isinstance(node.get("properties"), dict):
            raise AlignmentError("来源节点缺少名称或完整属性", 422)
        ids.add(node["id"])
    edges = set()
    for edge in graph["edges"]:
        if (
            not isinstance(edge, dict)
            or not isinstance(edge.get("id"), str)
            or not edge["id"]
            or edge["id"] in edges
        ):
            raise AlignmentError("来源图谱包含缺失或重复的关系 ID", 422)
        if edge.get("source") not in ids or edge.get("target") not in ids:
            raise AlignmentError("来源图谱存在缺失的关系端点，拒绝不完整图谱", 422)
        if not isinstance(edge.get("properties"), dict):
            raise AlignmentError("来源关系缺少完整属性", 422)
        edges.add(edge["id"])
    expected = graph.get("summary", {})
    for key, actual in (("node_count", len(ids)), ("edge_count", len(edges))):
        if key in expected and expected[key] != actual:
            raise AlignmentError("来源图谱计数与全量记录不一致，未执行消歧", 409)
    return graph


def brief(node):
    return NodeBrief(id=node["id"], name=node["name"], type=str(node.get("type", "")))


def attributes(node):
    props = node["properties"]
    fields = props.get("fields")
    return fields if isinstance(fields, dict) else props


def source_records(node):
    """Read prior merged observations for comparison without modifying the canonical."""
    stack, seen, records = [node], set(), []
    while stack:
        record = stack.pop()
        if not isinstance(record, dict) or not isinstance(record.get("properties"), dict):
            continue
        previous = record.get("resolution", {}).get("source_nodes", [])
        if isinstance(previous, list):
            stack.extend(reversed(previous))
        signature = digest(
            {
                "id": record.get("id"),
                "properties": record["properties"],
                "source_context": record.get("source_context"),
            }
        )
        if signature not in seen:
            seen.add(signature)
            records.append(record)
    return records


def corpus_from_graph(graph):
    """Make a comparison view, leaving evidence and identity strings unchanged."""
    mentions = []
    for node in graph["nodes"]:
        contexts, aliases, descriptions, assertions = [], [], [], {}
        for record in source_records(node):
            props = record["properties"]
            context = json.dumps(props, ensure_ascii=False, sort_keys=True)
            source_context = record.get("source_context")
            if isinstance(source_context, str) and source_context.strip():
                context = (
                    "原始文本：\n"
                    + source_context
                    + "\n结构化记录（可能含模型生成描述）：\n"
                    + context
                )
            contexts.append(context)
            if record.get("name") and record["name"] != node["name"]:
                aliases.append(record["name"])
            raw_aliases = props.get("aliases", [])
            if isinstance(raw_aliases, str):
                raw_aliases = [raw_aliases]
            if isinstance(raw_aliases, list):
                aliases.extend(
                    value for value in raw_aliases if isinstance(value, str) and value.strip()
                )
            descriptions.append(str(props.get("description", "") or ""))
            # Arbitrary source fields are retrieval hints, never trusted assertions.
            for key, value in attributes(record).items():
                if (
                    key.lower() in IDENTIFIER_FIELDS
                    and isinstance(value, str)
                    and value.strip()
                    and value in json.dumps(value, ensure_ascii=False)
                ):
                    assertions[key.lower(), value] = {
                        "namespace": key.lower(),
                        "value": value,
                        "quote": json.dumps(value, ensure_ascii=False),
                        "verified": False,
                    }
        mentions.append(
            {
                "mention_id": node["id"],
                "name": node["name"] or node["id"],
                "type": str(node.get("type", "")),
                "source_id": graph["source_id"],
                "context": "\n\n".join(dict.fromkeys(contexts)),
                "description": "\n".join(dict.fromkeys(descriptions)),
                "aliases": list(dict.fromkeys(aliases)),
                "identifiers": list(assertions.values()),
            }
        )
    if not mentions:
        return Corpus(namespace=f"{graph['source_kind']}:{graph['source_id']}", mentions=())
    return Corpus.from_dict(
        {
            "schema_version": "er-corpus-v1",
            "namespace": f"{graph['source_kind']}:{graph['source_id']}",
            "mentions": mentions,
        }
    )


def conflicts(nodes):
    nodes = [record for node in nodes for record in source_records(node)]
    fields = {}
    for node in nodes:
        for key, value in attributes(node).items():
            if value is not None and value != "":
                fields.setdefault(key, []).append({"node_id": node["id"], "value": value})
    result = []
    for key, values in sorted(fields.items()):
        if len({digest(row["value"]) for row in values}) > 1:
            result.append(Conflict(field=key, values=values))
    for key in ("type", "boid"):
        values = [{"node_id": node["id"], "value": node[key]} for node in nodes if node.get(key)]
        if len({digest(row["value"]) for row in values}) > 1:
            result.append(Conflict(field=f"@{key}", values=values))
    return result


REASONS = {
    "NO_IDENTITY_EVIDENCE": "现有证据不足以确认同一实体，保留原节点并等待人工核验",
    "MODEL_MATCH_REQUIRES_REVIEW": "模型提出同一实体建议，需要人工确认后才合并",
    "JUDGE_FAILED": "模型判断失败，仍保留候选与原始证据供人工核验",
    "MODEL_BUDGET_EXHAUSTED": "本轮模型判断预算已用尽，该候选等待人工核验",
    "CANDIDATE_OVERFLOW": "候选数量达到上限，需要扩大核验范围",
    "TRUSTED_IDENTIFIER_CONFLICT": "已核验身份标识冲突",
    "SOURCE_CONTEXT_EXCEEDS_PILOT_BUDGET": "原始上下文超过模型比较预算，保留完整证据供人工核验",
}


def candidate_from_decision(decision, by_id, mentions):
    ids = [decision["left"], decision["right"]]
    nodes = [by_id[key] for key in ids]
    left, right = (mentions[key] for key in ids)
    left_names = {normalize(name) for name in (left.name, *left.aliases)} - {""}
    right_names = {normalize(name) for name in (right.name, *right.aliases)} - {""}
    score = max(
        (SequenceMatcher(None, a, b).ratio() for a in left_names for b in right_names), default=0
    )
    reasons = []
    if left_names & right_names:
        reasons.append("名称或已有别名一致；名称一致本身不能证明身份")
    elif score:
        reasons.append(f"名称/别名字符相似度 {score:.3f}（不是正确概率）")
    common_ids = {(i.namespace, i.value) for i in left.identifiers} & {
        (i.namespace, i.value) for i in right.identifiers
    }
    if common_ids:
        reasons.append("存在相同的来源标识字段，编号域与可靠性仍需人工核验")
    if terms(left.description) & terms(right.description):
        reasons.append("描述存在共同词项，可用于定位上下文")
    reasons.append(REASONS.get(decision["reason"], decision["reason"]))
    if left.type and right.type and left.type != right.type:
        reasons.append("对象类型不同，请确认身份层级，避免把账户、人员和公司合并")
    return Candidate(
        id=digest(sorted(ids))[:32],
        node_ids=ids,
        nodes=[brief(node) for node in nodes],
        score=round(score, 6),
        reasons=reasons,
        evidence={
            **deepcopy(decision),
            "sources": [
                {
                    "node_id": node["id"],
                    "text_unit_ids": list(
                        dict.fromkeys(
                            unit_id
                            for record in source_records(node)
                            for unit_id in record.get("source_text_unit_ids", [])
                        )
                    ),
                    "has_source_context": any(
                        record.get("source_context") for record in source_records(node)
                    ),
                    "evidence_status": node.get(
                        "source_evidence_status",
                        "available" if node.get("source_context") else "structured_record",
                    ),
                    "prior_source_ids": [record["id"] for record in source_records(node)],
                }
                for node in nodes
            ],
        },
        conflicts=conflicts(nodes),
    )
