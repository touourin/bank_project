"""Source-reference checks for governed rules; quotes still require human review."""

from collections.abc import Mapping
from typing import Any

PARAMETERS: dict[str, tuple[set[str], set[str]]] = {
    "cash_aggregate_threshold": (
        {
            "threshold",
            "n_min",
            "max_single_le_threshold",
            "timezone",
            "subject_dimension",
            "cash_scope",
            "status_filter",
        },
        set(),
    ),
    "counterparty_region": ({"regions", "status_filter"}, {"transaction_type"}),
    "bo_scoped_aggregate": (
        {"n_min", "status_filter", "group_by"},
        {"txn_type", "amount_min", "total_min"},
    ),
}
LIVE_PREDICATES = set(PARAMETERS)
SOURCE_POLICY_VERSION = "bank.risk.source-references.v1"


def source_texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for child in value.values() for text in source_texts(child)]
    if isinstance(value, list):
        return [text for child in value for text in source_texts(child)]
    return []


def binding_issues(
    binding: Mapping[str, Any], rule_pack: Mapping[str, Any]
) -> list[dict[str, str]]:
    """Require identifiable source excerpts, without claiming semantic entailment."""
    body = rule_pack.get("body") or []
    if not isinstance(body, list) or len(body) != 1 or not isinstance(body[0], dict):
        return [{"field": "body", "reason": "invalid_rule"}]
    predicate = body[0].get("predicate")
    params = body[0].get("params") or {}
    if not isinstance(params, dict):
        return [{"field": "params", "reason": "invalid_parameters"}]
    issues = []
    for field in ("dataset_revision", "source_node_id", "dimension_hash", "why"):
        if not binding.get(field):
            issues.append({"field": field, "reason": "source_missing"})
    if predicate not in PARAMETERS:
        issues.append({"field": "predicate", "reason": "unsupported_predicate"})
    required, optional = PARAMETERS.get(
        predicate, (set(), set(params) - {"start", "end", "bo_scope"})
    )
    for field in sorted(required - set(params)):
        issues.append({"field": field, "reason": "parameter_missing"})
    for field in sorted(set(params) - required - optional - {"bo_scope"}):
        issues.append({"field": field, "reason": "unsupported_rule_parameter"})
    refs = binding.get("parameter_sources")
    refs = refs if isinstance(refs, dict) else {}
    texts = source_texts(binding.get("why"))
    for field in sorted({"predicate"} | (set(params) - {"bo_scope", "start", "end"})):
        ref = refs.get(field)
        quote = ref.get("quote") if isinstance(ref, dict) else None
        if not isinstance(quote, str) or not quote.strip():
            issues.append({"field": field, "reason": "parameter_source_missing"})
        elif not any(quote in text for text in texts):
            issues.append({"field": field, "reason": "quote_not_in_source"})
    return issues
