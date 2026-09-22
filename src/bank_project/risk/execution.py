"""Read published BankAlignedInstance snapshots and evaluate fixed risk predicates."""

import hashlib
import json
import re
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext

from bank_project.alignment.models import AlignmentError

from .compiler import (
    COMPILER_POLICY_VERSION,
    FIELD_METADATA,
    RuleCompiler,
    required_fields,
    scope_ids,
    window,
)

QUERY_POLICY_VERSION = "bank.risk.execution.v1"

_SCOPE_COUNT = """MATCH (n:BankAlignedInstance {version:$version})
WHERE n.concept_id IN $bo_scope RETURN count(n) AS count"""
_SCOPE_PAGE = """MATCH (n:BankAlignedInstance {version:$version})
WHERE n.concept_id IN $bo_scope AND n.id>$after
RETURN n.id AS id, n.data AS data ORDER BY id LIMIT $limit"""
LOCAL_DAY = timezone(timedelta(hours=8))


def _minor(value, node_id):
    """Source amounts are explicitly mapped CNY major units; never use floats."""
    try:
        text = str(value)
        if (
            isinstance(value, bool)
            or len(text) > 100
            or not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text)
        ):
            raise ValueError()
        with localcontext() as context:
            context.prec = 120
            amount = Decimal(text) * 100
            if (
                not amount.is_finite()
                or amount != amount.to_integral_value()
                or abs(amount) > 10**18
            ):
                raise ValueError()
            return int(amount)
    except (ValueError, InvalidOperation) as exc:
        raise AlignmentError(
            f"实例 {node_id} 的金额必须是人民币元文本，最多两位小数且绝对值不超过 10^16 元"
        ) from exc


def _money(value):
    return {"value": value, "unit": "CNY_MINOR"}


class RiskExecutor:
    # These bound the complete input, not just the displayed witnesses.
    MAX_SCOPE_ROWS = 50000
    MAX_SCOPE_BYTES = 32 * 1024 * 1024
    PAGE_SIZE = 200
    MAX_HITS = 200
    EVIDENCE_PER_HIT = 20
    FIELD_SAMPLE_ROWS = 200

    def __init__(self, graph):
        self.graph = graph
        self.compiler = RuleCompiler()

    def _read(
        self,
        graph_version,
        bo_scope,
        *,
        sample=False,
        expected_revision=None,
        expected_ontology_id=None,
    ):
        bo_scope = scope_ids(bo_scope)
        if not isinstance(graph_version, str) or not graph_version:
            raise AlignmentError("必须指定已发布的业务图谱版本")
        browser = self.graph.browser
        with browser.session() as session:
            summary = browser.version(session, graph_version)
            revision = summary.revision
            if expected_revision is not None and revision != expected_revision:
                raise AlignmentError(
                    "业务图谱采用的本体版本与规则依据不一致，请选择使用相同本体版本的图谱", 409
                )
            # Legacy publications did not persist ontology IDs; their revision
            # remains the compatibility boundary. New publications bind both.
            ontology_id = summary.ontology_id
            if expected_ontology_id and ontology_id and ontology_id != expected_ontology_id:
                raise AlignmentError("业务图谱与规则依据来自不同本体，请选择相同本体的图谱", 409)
            count_rows = browser.query(
                session, _SCOPE_COUNT, version=graph_version, bo_scope=bo_scope
            )
            total = int(count_rows[0]["count"])
            if total > self.MAX_SCOPE_ROWS and not sample:
                raise AlignmentError(
                    f"概念作用域含 {total} 个实例，超过完整计算上限 {self.MAX_SCOPE_ROWS}；请缩小概念作用域。未计算部分结果"
                )
            maximum = min(total, self.FIELD_SAMPLE_ROWS) if sample else total
            nodes, after, byte_count = [], "", 0
            while len(nodes) < maximum:
                limit = min(self.PAGE_SIZE, maximum - len(nodes))
                rows = browser.query(
                    session,
                    _SCOPE_PAGE,
                    version=graph_version,
                    bo_scope=bo_scope,
                    after=after,
                    limit=limit,
                )
                if not rows or len(rows) > limit:
                    raise AlignmentError("图谱作用域读取不完整，未计算部分结果", 409)
                for row in rows:
                    raw = row.get("data")
                    if not isinstance(raw, str):
                        raise AlignmentError("图谱实例缺少可读取的原始字段", 409)
                    byte_count += len(raw.encode("utf-8"))
                    if byte_count > self.MAX_SCOPE_BYTES:
                        raise AlignmentError(
                            "图谱作用域数据超过完整读取预算，请缩小概念作用域；未计算部分结果"
                        )
                    try:
                        node = json.loads(raw)
                    except (ValueError, TypeError) as exc:
                        raise AlignmentError("图谱实例字段格式错误，未计算部分结果", 409) from exc
                    if (
                        not isinstance(node, dict)
                        or not isinstance(node.get("id"), str)
                        or node["id"] != row.get("id")
                        or node["id"] <= after
                        or node.get("concept_id") not in bo_scope
                        or not isinstance(node.get("fields"), dict)
                    ):
                        raise AlignmentError("图谱实例与已发布作用域不一致，未计算部分结果", 409)
                    after = node["id"]
                    nodes.append(node)
            # Published versions are immutable; a mismatch means an incomplete read.
            if not sample and len(nodes) != total:
                raise AlignmentError("图谱作用域读取不完整，未计算部分结果", 409)
        return nodes, total, revision

    def fields(self, graph_version, bo_scope, *, expected_revision=None, expected_ontology_id=None):
        bo_scope = scope_ids(bo_scope)
        nodes, total, revision = self._read(
            graph_version,
            bo_scope,
            sample=True,
            expected_revision=expected_revision,
            expected_ontology_id=expected_ontology_id,
        )
        fields = {}
        for node in nodes:
            for name, value in node["fields"].items():
                item = fields.setdefault(name, {"name": name, "present_count": 0, "samples": []})
                if value is not None and value != "":
                    item["present_count"] += 1
                    sample = str(value)[:200]
                    if sample not in item["samples"] and len(item["samples"]) < 3:
                        item["samples"].append(sample)
        return {
            "graph_version": graph_version,
            "ontology_revision": revision,
            "bo_scope": bo_scope,
            "fields": sorted(fields.values(), key=lambda item: item["name"]),
            "sampled_count": len(nodes),
            "total_count": total,
            "truncated": total > len(nodes),
            "canonical_fields": deepcopy(FIELD_METADATA),
        }

    @staticmethod
    def _mapping(mapping, required):
        if not isinstance(mapping, dict) or not mapping:
            raise AlignmentError("执行需要显式的原始字段映射")
        if any(key not in FIELD_METADATA for key in mapping):
            raise AlignmentError("字段映射包含未知的标准字段")
        if any(
            not isinstance(value, str) or not value or len(value) > 512
            for value in mapping.values()
        ):
            raise AlignmentError("字段映射必须指向有效的原始字段名")
        missing = set(required) - set(mapping)
        if missing:
            raise AlignmentError("请显式映射所需字段：" + ", ".join(sorted(missing)))
        if len(set(mapping.values())) != len(mapping):
            raise AlignmentError("不同标准字段不能映射到同一原始字段")

    @staticmethod
    def _value(node, mapping, canonical):
        value = node["fields"].get(mapping[canonical])
        if value is None or isinstance(value, (list, dict, bool)) or str(value).strip() == "":
            raise AlignmentError(
                f"实例 {node['id']} 缺少有效字段 {canonical}（{mapping[canonical]}）；未跳过不完整实例"
            )
        return str(value)

    def execute(
        self,
        rule_pack,
        graph_version,
        field_mapping,
        start,
        end,
        *,
        expected_revision=None,
        expected_ontology_id=None,
    ):
        first, last = window(start, end)
        working = self.compiler.validate(rule_pack)
        params = working["body"][0]["params"]
        # Only this execution copy receives the user-selected time window.
        params.update(start=start, end=end)
        working = self.compiler.validate(working, require_window=True)
        predicate = working["body"][0]["predicate"]
        params = working["body"][0]["params"]
        bo_scope = scope_ids(params.get("bo_scope"))
        required = required_fields(predicate, params)
        self._mapping(field_mapping, required)
        nodes, total, revision = self._read(
            graph_version,
            bo_scope,
            expected_revision=expected_revision,
            expected_ontology_id=expected_ontology_id,
        )
        groups = defaultdict(list)
        region_hits, within_window, matched = [], 0, 0
        for node in nodes:
            timestamp = self._value(node, field_mapping, "occurred_at")
            try:
                occurred = datetime.fromisoformat(timestamp)
                if occurred.utcoffset() is None:
                    raise ValueError()
            except (ValueError, TypeError) as exc:
                raise AlignmentError(
                    f"实例 {node['id']} 的 occurred_at 必须是带时区的 ISO8601 时间"
                ) from exc
            if not first <= occurred < last:
                continue
            within_window += 1
            status = self._value(node, field_mapping, "status")
            if status != params["status_filter"]:
                continue
            values = {field: self._value(node, field_mapping, field) for field in required}
            if (
                predicate == "cash_aggregate_threshold"
                and values["txn_type"] != params["cash_scope"]
            ):
                continue
            if predicate == "counterparty_region":
                if values["counterparty_region"] not in params["regions"]:
                    continue
                if (
                    "transaction_type" in params
                    and values["txn_type"] != params["transaction_type"]
                ):
                    continue
            if (
                predicate == "bo_scoped_aggregate"
                and "txn_type" in params
                and values["txn_type"] != params["txn_type"]
            ):
                continue
            minor = _minor(values["amount"], node["id"])
            if "amount_min" in params and minor < params["amount_min"]["value"]:
                continue
            matched += 1
            values["amount"] = _money(minor)
            evidence = {
                "id": node["id"],
                "concept_id": node["concept_id"],
                "table_id": node.get("table_id"),
                "source_row": node.get("source_row"),
                "fields": values,
            }
            account = values["account_id"]
            if predicate == "counterparty_region":
                region_hits.append(
                    {
                        "id": node["id"],
                        "account_id": account,
                        "region": values["counterparty_region"],
                        "count": 1,
                        "total": _money(minor),
                        "transactions": [evidence],
                    }
                )
            else:
                day = (
                    occurred.astimezone(LOCAL_DAY).date().isoformat()
                    if predicate == "cash_aggregate_threshold"
                    else ""
                )
                groups[(account, day)].append((minor, evidence))
        hits = region_hits
        evidence_truncated = False
        for (account, day), entries in groups.items():
            amount = sum(row[0] for row in entries)
            if len(entries) < params["n_min"]:
                continue
            if predicate == "cash_aggregate_threshold":
                threshold = params["threshold"]["value"]
                if amount <= threshold or max(row[0] for row in entries) > threshold:
                    continue
            elif "total_min" in params and amount < params["total_min"]["value"]:
                continue
            identifier = hashlib.sha256(
                json.dumps([predicate, account, day], ensure_ascii=False).encode()
            ).hexdigest()[:24]
            hit = {
                "id": identifier,
                "account_id": account,
                "count": len(entries),
                "total": _money(amount),
                "transactions": [row[1] for row in entries[: self.EVIDENCE_PER_HIT]],
                "evidence_truncated": len(entries) > self.EVIDENCE_PER_HIT,
            }
            evidence_truncated |= hit["evidence_truncated"]
            if day:
                hit.update(day=day, max_single=_money(max(row[0] for row in entries)))
            hits.append(hit)
        hits.sort(key=lambda hit: (-hit["total"]["value"], hit["id"]))
        query_plan = {
            "query_policy_version": QUERY_POLICY_VERSION,
            "compiler_policy_version": COMPILER_POLICY_VERSION,
            "statements": {"scope_count": _SCOPE_COUNT, "scope_page": _SCOPE_PAGE},
            "rule_pack": working,
            "field_mapping": field_mapping,
            "graph_version": graph_version,
            "ontology_revision": revision,
            "limits": {
                "scope_rows": self.MAX_SCOPE_ROWS,
                "scope_bytes": self.MAX_SCOPE_BYTES,
                "page_size": self.PAGE_SIZE,
                "hits": self.MAX_HITS,
                "evidence_per_hit": self.EVIDENCE_PER_HIT,
            },
        }
        query_hash = hashlib.sha256(
            json.dumps(
                query_plan,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        return {
            "predicate": predicate,
            "graph_version": graph_version,
            "start": start,
            "end": end,
            "query_policy_version": QUERY_POLICY_VERSION,
            "query_hash": query_hash,
            "bo_scope": bo_scope,
            "ontology_revision": revision,
            "field_mapping": deepcopy(field_mapping),
            "counts": {
                "scanned": total,
                "within_window": within_window,
                "matched_transactions": matched,
                "hit_count": len(hits),
            },
            "hits": hits[: self.MAX_HITS],
            "truncation": {
                "scope": False,
                "hits": len(hits) > self.MAX_HITS,
                "evidence": evidence_truncated,
            },
            "limits": {
                "scope": self.MAX_SCOPE_ROWS,
                "hit": self.MAX_HITS,
                "evidence_per_hit": self.EVIDENCE_PER_HIT,
            },
        }
