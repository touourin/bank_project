"""Adapt complete GraphRAG/DB snapshots without treating raw attributes as instructions."""

import json
from copy import deepcopy
from difflib import SequenceMatcher
from itertools import combinations

from bank_project.alignment.models import AlignmentError

from .engine.candidates import normalize, recall_names, shared_record_key, terms
from .engine.contracts import Corpus, digest, require
from .engine.identity_guard import assess_identity
from .models import Candidate, Conflict, NodeBrief
from .record_recall import EVENT_ID_FIELDS, database_recall, field_key, is_event

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

MISSING_SOURCE_CONTEXT = "[SOURCE_TEXT_UNAVAILABLE]"


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
                "source_span": record.get("source_span"),
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
        records = source_records(node)
        contexts, aliases, descriptions, assertions = [], [], [], {}
        context_offsets, located_spans = {}, set()
        for record in records:
            props = record["properties"]
            context = json.dumps(props, ensure_ascii=False, sort_keys=True)
            source_context = record.get("source_context")
            if graph["source_kind"] == "graphrag":
                # Extracted titles/descriptions are retrieval hints, never source
                # text against which an LLM citation can validate itself.
                context = source_context if isinstance(source_context, str) else ""
            if context.strip():
                if context not in context_offsets:
                    context_offsets[context] = sum(len(value) + 2 for value in contexts)
                    contexts.append(context)
                # Offsets refer to this exact original text, never an extracted
                # property or the first occurrence of a repeated entity name.
                span = record.get("source_span")
                if graph["source_kind"] == "graphrag" and span is not None:
                    require(
                        isinstance(span, (list, tuple))
                        and len(span) == 2
                        and all(type(value) is int for value in span),
                        "Graph source_span must contain two integer offsets",
                    )
                    start, end = span
                    require(0 <= start < end <= len(context), "Graph source_span is out of bounds")
                    require(
                        context[start:end] == record.get("name"),
                        "Graph source_span must match its source record name",
                    )
                    offset = context_offsets[context]
                    located_spans.add((offset + start, offset + end))
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
            event = graph["source_kind"] == "database" and is_event(record)
            for key, value in attributes(record).items():
                own_identifier = (
                    field_key(key) in EVENT_ID_FIELDS if event else key.lower() in IDENTIFIER_FIELDS
                )
                if own_identifier and isinstance(value, str) and value.strip() and value in context:
                    quote = json.dumps(value, ensure_ascii=False)
                    assertions[key.lower(), value] = {
                        "namespace": key.lower(),
                        "value": value,
                        "quote": quote if quote in context else value,
                        "verified": False,
                    }
        mention = {
            "mention_id": node["id"],
            "name": node["name"] or node["id"],
            "type": str(node.get("type", "")),
            "source_id": graph["source_id"],
            "context": "\n\n".join(contexts) or MISSING_SOURCE_CONTEXT,
            "description": "\n".join(dict.fromkeys(descriptions)),
            "aliases": list(dict.fromkeys(aliases)),
            "identifiers": list(assertions.values()),
        }
        if graph["source_kind"] == "database":
            recall = database_recall(records)
            if recall is not None:
                mention["recall"] = recall
        if len(located_spans) == 1:
            start, end = next(iter(located_spans))
            # A merged node can retain differently named source observations;
            # a single span can only ground the canonical name itself.
            if mention["context"][start:end] == mention["name"]:
                mention["source_span"] = [start, end]
        mentions.append(mention)
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
    "NO_IDENTITY_EVIDENCE": "未获得有效的模型身份判断，未生成合并建议，保留独立节点",
    "MODEL_MATCH_REQUIRES_REVIEW": "模型提出同一实体建议，需要人工确认后才合并",
    "IDENTITY_DIMENSION_CONFLICT": "年份、期间、会议序号或产品型号存在身份冲突，应保留为不同实体",
    "IDENTITY_DIMENSION_UNSPECIFIED": "身份限定未对齐，缺少证明为同一实体的依据，自动不合并",
    "JUDGE_FAILED": "模型判断失败，未生成合并建议；原始证据保留在分析记录中",
    "MODEL_BUDGET_EXHAUSTED": "本轮模型判断预算已用尽，该记录未形成合并建议",
    "CANDIDATE_OVERFLOW": "候选数量达到上限，需要扩大核验范围",
    "TRUSTED_IDENTIFIER_CONFLICT": "已核验身份标识冲突",
    "SOURCE_CONTEXT_EXCEEDS_PILOT_BUDGET": "原始上下文超过模型比较预算，保留完整证据供人工核验",
    "SOURCE_EVIDENCE_TOO_SHORT": "可引用的原文过短，尚不足以形成有效身份判断",
}


def candidate_from_decision(decision, by_id, mentions, source_kind):
    ids = [decision["left"], decision["right"]]
    nodes = [by_id[key] for key in ids]
    left, right = (mentions[key] for key in ids)
    left_names = {normalize(name) for name in recall_names(left)} - {""}
    right_names = {normalize(name) for name in recall_names(right)} - {""}
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
    if shared_record_key(left, right):
        reasons.append("参与方、事件类型和发生时间一致，仅为疑似重复事件，仍需核对来源证据")
    if terms(left.description) & terms(right.description):
        reasons.append("描述存在共同词项，可用于定位上下文")
    reasons.append(REASONS.get(decision["reason"], decision["reason"]))
    if left.type and right.type and left.type != right.type:
        reasons.append("对象类型不同，请确认身份层级，避免把账户、人员和公司合并")
    candidate = Candidate(
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
    annotate_quotes(candidate, by_id, source_kind)
    return annotate_identity(candidate)


def quote_location(quote, records, source_kind):
    """Locate a literal quote, distinguishing uploaded text from generated fields."""
    if not isinstance(quote, str) or not quote.strip():
        return {"origin": "unverified", "message": "模型未提供有效引用"}
    for record in records:
        context = record.get("source_context")
        if isinstance(context, str) and quote in context:
            offset = context.index(quote)
            return {
                "origin": "source_text",
                "node_id": record["id"],
                "text_unit_ids": record.get("source_text_unit_ids", []),
                "excerpt": context[max(0, offset - 100) : offset + len(quote) + 150],
                "message": "引用可在该节点关联的原文片段中逐字找到",
            }
    for record in records:
        props = record["properties"]
        if quote in json.dumps(props, ensure_ascii=False, sort_keys=True):
            return {
                "origin": "node_attribute" if source_kind == "graphrag" else "source_record",
                "node_id": record["id"],
                "fields": [
                    key
                    for key, value in props.items()
                    if quote in json.dumps(value, ensure_ascii=False, sort_keys=True)
                ],
                "message": (
                    "仅在抽取后的节点属性中找到，未在该节点关联的原文中找到"
                    if source_kind == "graphrag"
                    else "引用来自数据库来源记录的字段"
                ),
            }
    return {"origin": "unverified", "message": "未在该节点的来源证据中找到引用"}


def annotate_quotes(candidate, by_id, source_kind):
    evidence = candidate.evidence
    if evidence.get("origin") != "model":
        return
    locations = {}
    for side in ("left", "right"):
        node_id = evidence.get(side) or candidate.node_ids[0 if side == "left" else 1]
        records = source_records(by_id[node_id]) if node_id in by_id else []
        locations[side] = quote_location(evidence.get(f"{side}_quote"), records, source_kind)
    evidence["quote_validation"] = {
        "version": "source-quotes-v1",
        "supported": all(
            location["origin"] in {"source_text", "source_record"}
            for location in locations.values()
        ),
        **locations,
    }


def annotate_identity(candidate):
    """Present current identity checks without changing historical model decisions."""
    legacy = {
        "现有证据不足以确认同一实体，保留原节点并等待人工核验": REASONS["NO_IDENTITY_EVIDENCE"],
        "模型判断失败，仍保留候选与原始证据供人工核验": REASONS["JUDGE_FAILED"],
        "本轮模型判断预算已用尽，该候选等待人工核验": REASONS["MODEL_BUDGET_EXHAUSTED"],
    }
    candidate.reasons = [legacy.get(reason, reason) for reason in candidate.reasons]
    assessments = [
        a
        for left, right in combinations(candidate.nodes, 2)
        if (a := assess_identity(left, right)) is not None
    ]
    guard = next(
        (a for a in assessments if a["verdict"] == "different"),
        assessments[0] if assessments else None,
    )
    if guard is not None:
        previous = candidate.evidence.get("identity_guard")
        previous_message = previous.get("message") if isinstance(previous, dict) else None
        # Retire generated v1 review instructions, while keeping historical model
        # decisions and their rationale in the evidence unchanged.
        candidate.reasons = [
            reason
            for reason in candidate.reasons
            if reason
            not in {
                previous_message,
                "身份限定不完整，需核对原文，不能仅凭名称或金额相同合并",
                REASONS["NO_IDENTITY_EVIDENCE"],
                REASONS["MODEL_MATCH_REQUIRES_REVIEW"],
                REASONS["JUDGE_FAILED"],
                REASONS["MODEL_BUDGET_EXHAUSTED"],
                REASONS["SOURCE_CONTEXT_EXCEEDS_PILOT_BUDGET"],
            }
        ]
        candidate.evidence = {
            **candidate.evidence,
            "identity_guard": guard,
            "effective_verdict": guard["verdict"],
        }
        if guard["message"] not in candidate.reasons:
            candidate.reasons = [*candidate.reasons, guard["message"]]
        if guard["block_merge"] and candidate.status in {"pending", "not_recommended"}:
            # Identity conflict or missing scope completes the no-merge decision.
            # Preserve existing human decisions/audits.
            candidate.status = "excluded"
    else:
        candidate.evidence = {
            **candidate.evidence,
            "effective_verdict": candidate.evidence.get("proposal")
            or candidate.evidence.get("verdict", "uncertain"),
        }
    if candidate.status in {"pending", "not_recommended"}:
        candidate.status = (
            "pending" if model_recommends_merge(candidate.evidence) else "not_recommended"
        )
    validation = candidate.evidence.get("quote_validation", {})
    if (
        validation.get("supported") is False
        and (candidate.evidence.get("proposal") or candidate.evidence.get("verdict")) == "same"
    ):
        candidate.reasons = [
            reason
            for reason in candidate.reasons
            if reason != REASONS["MODEL_MATCH_REQUIRES_REVIEW"]
        ]
        message = "模型引用未通过来源校验，不作为有效合并建议；节点属性不能冒充文档原文"
        if message not in candidate.reasons:
            candidate.reasons.append(message)
    return candidate


def model_recommends_merge(evidence):
    """Only a validated model identity match may enter the default review queue."""
    return (
        evidence.get("origin") == "model"
        and (evidence.get("proposal") or evidence.get("verdict")) == "same"
        and not evidence.get("identity_guard", {}).get("block_merge")
        and evidence.get("quote_validation", {}).get("supported") is not False
        and all(
            isinstance(evidence.get(key), str) and evidence[key].strip()
            for key in ("left_quote", "right_quote")
        )
    )


def annotate_run(run, graph=None):
    """Apply the current review policy to historical metadata without rewriting evidence."""
    if graph is not None:
        by_id = {node["id"]: node for node in graph["nodes"]}
        for candidate in run.candidates:
            annotate_quotes(candidate, by_id, run.source_kind)
    run.candidates = [annotate_identity(candidate) for candidate in run.candidates]
    if run.status == "ready":
        run.summary.pending_count = sum(c.status == "pending" for c in run.candidates)
        run.summary.excluded_count = sum(c.status == "excluded" for c in run.candidates)
        run.summary.merged_count = sum(c.status == "merged" for c in run.candidates)
        run.summary.rejected_count = sum(c.status == "rejected" for c in run.candidates)
        run.summary.not_recommended_count = sum(
            c.status == "not_recommended" for c in run.candidates
        )
        run.progress = (
            f"分析完成；{run.summary.pending_count} 组模型合并建议待确认，"
            f"{run.summary.excluded_count} 组自动不合并，"
            f"{run.summary.not_recommended_count} 组未形成合并建议，"
            f"{run.summary.merged_count} 组合并；原图保持完整"
        )
    if "warnings" in run.diagnostics:
        run.diagnostics["warnings"] = [
            warning.replace("保留待人工校验", "未生成合并建议，详情保留在分析记录中")
            for warning in run.diagnostics["warnings"]
        ]
    return run
