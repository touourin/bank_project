"""Allowlisted RulePack validation, independent of model output and graph storage."""

from copy import deepcopy
from datetime import datetime, timedelta

from bank_project.alignment.models import AlignmentError

from .sources import PARAMETERS

COMPILER_POLICY_VERSION = "bank.risk.compiler.v1"
MAX_BO_SCOPE = 1000
FORBIDDEN_KEYS = {
    "query",
    "cypher",
    "fixture",
    "is_fixture",
    "answer",
    "expected_label",
    "insert_order",
    "insertion_order",
    "id_prefix",
    "id_modulo",
    "id_sequence",
    "arithmetic_sequence",
    "geometric_sequence",
    "constant_time_delta",
    "sequential_id",
}
BASE_FIELDS = ["account_id", "occurred_at", "amount", "status"]
FIELD_METADATA = {
    "account_id": {"label": "账户标识", "format": "text"},
    "occurred_at": {"label": "交易时间", "format": "ISO8601，必须含时区"},
    "amount": {"label": "交易金额", "format": "人民币元，最多两位小数；不使用浮点计算"},
    "status": {"label": "交易状态", "format": "与 status_filter 精确匹配"},
    "txn_type": {"label": "交易类型", "format": "与规则类型精确匹配"},
    "counterparty_region": {"label": "交易对手地区", "format": "与 regions 精确匹配"},
}


def _walk_keys(value):
    if isinstance(value, dict):
        return {str(key).lower() for key in value} | {
            key for item in value.values() for key in _walk_keys(item)
        }
    if isinstance(value, list):
        return {key for item in value for key in _walk_keys(item)}
    return set()


def window(start, end):
    try:
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError()
        first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if first.utcoffset() is None or last.utcoffset() is None:
            raise ValueError()
        if not first < last or last - first > timedelta(days=366):
            raise ValueError()
    except (ValueError, TypeError, OverflowError) as exc:
        raise AlignmentError("查询时间必须是带时区的 ISO8601，范围为正且不超过 366 天") from exc
    return first, last


def scope_ids(value):
    if (
        not isinstance(value, list)
        or not value
        or len(value) > MAX_BO_SCOPE
        or any(
            not isinstance(item, str) or not item or item != item.strip() or len(item) > 200
            for item in value
        )
    ):
        raise AlignmentError(f"bo_scope 必须包含 1–{MAX_BO_SCOPE} 个有效概念标识")
    return sorted(set(value))


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise AlignmentError(f"{label} 必须是非空文本且不超过 500 字符")


def _money(value, label):
    if (
        not isinstance(value, dict)
        or set(value) != {"value", "unit"}
        or value.get("unit") != "CNY_MINOR"
    ):
        raise AlignmentError(f"{label} 必须使用 {{value: 非负整数分, unit: CNY_MINOR}}")
    amount = value.get("value")
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= 10**18:
        raise AlignmentError(f"{label}.value 必须是 0 至 10^18 的整数分")


def required_fields(predicate, params):
    fields = BASE_FIELDS.copy()
    if (
        predicate == "cash_aggregate_threshold"
        or params.get("transaction_type") is not None
        or params.get("txn_type") is not None
    ):
        fields.append("txn_type")
    if predicate == "counterparty_region":
        fields.append("counterparty_region")
    return fields


def supported_predicates():
    descriptions = {
        "cash_aggregate_threshold": (
            "现金累计金额阈值",
            "同一账户在 +08:00 自然日的交易笔数达到下限，累计金额超过阈值且最大单笔不超过阈值。",
        ),
        "counterparty_region": (
            "交易对手地区",
            "窗口内交易状态、对手地区及可选交易类型与规则精确匹配。",
        ),
        "bo_scoped_aggregate": (
            "概念范围聚合",
            "概念作用域内按账户汇总，检查笔数及可选单笔、累计金额下限。",
        ),
    }
    text_schema = {"type": "string", "minLength": 1, "maxLength": 500}
    money_schema = {
        "type": "object",
        "required": ["value", "unit"],
        "additionalProperties": False,
        "properties": {
            "value": {"type": "integer", "minimum": 0, "maximum": 10**18},
            "unit": {"const": "CNY_MINOR"},
        },
    }
    properties = {
        "cash_aggregate_threshold": {
            "threshold": money_schema,
            "n_min": {"type": "integer", "minimum": 2},
            "max_single_le_threshold": {"const": True},
            "timezone": {"const": "+08:00"},
            "subject_dimension": {"const": "Account"},
            "cash_scope": text_schema,
            "status_filter": text_schema,
        },
        "counterparty_region": {
            "regions": {"type": "array", "minItems": 1, "maxItems": 500, "items": text_schema},
            "status_filter": text_schema,
            "transaction_type": text_schema,
        },
        "bo_scoped_aggregate": {
            "n_min": {"type": "integer", "minimum": 1},
            "status_filter": text_schema,
            "group_by": {"const": "account"},
            "txn_type": text_schema,
            "amount_min": money_schema,
            "total_min": money_schema,
        },
    }
    schemas = {
        predicate: {
            "type": "object",
            "additionalProperties": False,
            "required": sorted(PARAMETERS[predicate][0]),
            "properties": fields,
        }
        for predicate, fields in properties.items()
    }
    return [
        {
            "predicate": predicate,
            "name": name,
            "description": description,
            "required_params": sorted(PARAMETERS[predicate][0]),
            "optional_params": sorted(PARAMETERS[predicate][1]),
            "required_fields": required_fields(predicate, {}),
            "optional_fields": ["txn_type"] if predicate != "cash_aggregate_threshold" else [],
            "field_metadata": deepcopy(FIELD_METADATA),
            "parameter_schema": schemas[predicate],
        }
        for predicate, (name, description) in descriptions.items()
    ]


class RuleCompiler:
    def validate(self, rule_pack, require_window=False):
        if not isinstance(rule_pack, dict):
            raise AlignmentError("RulePack 必须是对象")
        forbidden = _walk_keys(rule_pack) & FORBIDDEN_KEYS
        if forbidden:
            raise AlignmentError(
                "RulePack 不允许任意查询或测试指纹字段：" + ", ".join(sorted(forbidden))
            )
        if set(rule_pack) != {"head", "body"}:
            raise AlignmentError("RulePack 只能包含 head 和 body")
        result = deepcopy(rule_pack)
        head, body = result.get("head"), result.get("body")
        if not isinstance(head, dict) or set(head) != {"risk_label", "semantics"}:
            raise AlignmentError("head 必须包含 risk_label 和 semantics")
        _text(head["risk_label"], "risk_label")
        if head["semantics"] != "pattern_not_intent":
            raise AlignmentError("风险规则只识别模式，semantics 必须为 pattern_not_intent")
        if (
            not isinstance(body, list)
            or len(body) != 1
            or not isinstance(body[0], dict)
            or set(body[0]) != {"predicate", "params"}
        ):
            raise AlignmentError("RulePack 必须且只能包含一个明确的 predicate 与 params")
        predicate, params = body[0]["predicate"], body[0]["params"]
        if not isinstance(predicate, str) or predicate not in PARAMETERS:
            raise AlignmentError("该谓词没有可执行的服务端模板")
        if not isinstance(params, dict):
            raise AlignmentError("params 必须是对象")
        required, optional = PARAMETERS[predicate]
        missing = required - set(params)
        if missing:
            raise AlignmentError("缺少明确的业务参数：" + ", ".join(sorted(missing)))
        unknown = set(params) - required - optional - {"start", "end", "bo_scope"}
        if unknown:
            raise AlignmentError("不支持的规则参数：" + ", ".join(sorted(unknown)))
        _text(params["status_filter"], "status_filter")
        if "bo_scope" in params:
            params["bo_scope"] = scope_ids(params["bo_scope"])
        elif predicate == "bo_scoped_aggregate":
            raise AlignmentError("bo_scoped_aggregate 需要明确的 bo_scope")
        if require_window or "start" in params or "end" in params:
            window(params.get("start"), params.get("end"))
        if predicate in {"cash_aggregate_threshold", "bo_scoped_aggregate"}:
            minimum = 2 if predicate == "cash_aggregate_threshold" else 1
            value = params["n_min"]
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise AlignmentError(f"n_min 必须是大于等于 {minimum} 的整数")
        if predicate == "cash_aggregate_threshold":
            if (
                params["timezone"] != "+08:00"
                or params["subject_dimension"] != "Account"
                or params["max_single_le_threshold"] is not True
            ):
                raise AlignmentError(
                    "现金累计规则仅支持 +08:00、Account 和 max_single_le_threshold=true"
                )
            _text(params["cash_scope"], "cash_scope")
            _money(params["threshold"], "threshold")
        elif predicate == "counterparty_region":
            regions = params["regions"]
            if not isinstance(regions, list) or not regions or len(regions) > 500:
                raise AlignmentError("regions 必须是 1–500 个地区的文本列表")
            for region in regions:
                _text(region, "regions")
            params["regions"] = sorted(set(regions))
        elif params["group_by"] != "account":
            raise AlignmentError("group_by 仅支持 account")
        for key in ("transaction_type", "txn_type"):
            if key in params:
                _text(params[key], key)
        for key in ("amount_min", "total_min"):
            if key in params:
                _money(params[key], key)
        return result
