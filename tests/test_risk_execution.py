"""Risk evaluation uses complete, explicitly mapped published graph snapshots."""

import json
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bank_project.alignment.models import AlignmentError
from bank_project.risk.compiler import RuleCompiler, supported_predicates
from bank_project.risk.execution import RiskExecutor
from bank_project.risk.sources import binding_issues

START = "2026-09-01T00:00:00+08:00"
END = "2026-09-02T00:00:00+08:00"
MAPPING = {
    "account_id": "账号",
    "amount": "金额元",
    "occurred_at": "时间",
    "status": "状态",
    "txn_type": "类型",
    "counterparty_region": "地区",
}


def rule(predicate="cash_aggregate_threshold", **changes):
    params = {
        "cash_aggregate_threshold": {
            "threshold": {"value": 30, "unit": "CNY_MINOR"},
            "n_min": 2,
            "max_single_le_threshold": True,
            "timezone": "+08:00",
            "subject_dimension": "Account",
            "cash_scope": "现金存入",
            "status_filter": "成功",
        },
        "counterparty_region": {"regions": ["地区甲"], "status_filter": "成功"},
        "bo_scoped_aggregate": {"n_min": 2, "group_by": "account", "status_filter": "成功"},
    }[predicate]
    params.update(bo_scope=["现金存入", "大额现金存入"], **changes)
    return {
        "head": {"risk_label": "人工审核的风险模式", "semantics": "pattern_not_intent"},
        "body": [{"predicate": predicate, "params": params}],
    }


def node(
    identifier,
    amount="0.20",
    *,
    timestamp="2026-09-01T02:00:00+08:00",
    account="账户甲",
    concept="现金存入",
    version="published",
    status="成功",
    region="地区甲",
    txn_type="现金存入",
):
    return {
        "version": version,
        "id": identifier,
        "concept_id": concept,
        "table_id": "交易表",
        "source_row": 2,
        "fields": {
            "账号": account,
            "金额元": amount,
            "时间": timestamp,
            "状态": status,
            "地区": region,
            "类型": txn_type,
        },
    }


class Browser:
    def __init__(self, nodes, versions=("published",)):
        self.nodes = nodes
        self.versions = versions
        self.queries = []

    @contextmanager
    def session(self):
        yield self

    def version(self, session, version):
        if version not in self.versions:
            raise AlignmentError("图谱版本不存在或尚未发布", 404)
        return SimpleNamespace(revision="ontology-v1", ontology_id=None)

    def query(self, session, text, **params):
        self.queries.append((text, params))
        assert "$version" in text and "$bo_scope" in text
        assert "BankAlignedInstance" in text
        rows = sorted(
            (
                n
                for n in self.nodes
                if n["version"] == params["version"] and n["concept_id"] in params["bo_scope"]
            ),
            key=lambda n: n["id"],
        )
        if "count(n)" in text:
            return [{"count": len(rows)}]
        assert "n.id>$after" in text and "LIMIT $limit" in text and "n.data AS data" in text
        return [{"id": n["id"], "data": json.dumps(n)} for n in rows if n["id"] > params["after"]][
            : params["limit"]
        ]


def executor(nodes):
    return RiskExecutor(SimpleNamespace(browser=Browser(nodes)))


def test_decimal_strict_threshold_scope_and_version():
    nodes = [
        node("1", "0.10"),
        node("2", "0.20", concept="大额现金存入"),
        node("3", "9999", concept="其他节点"),
        node("4", "9999", version="building"),
    ]
    worker = executor(nodes)
    original = rule()
    before = deepcopy(original)
    assert worker.execute(original, "published", MAPPING, START, END)["counts"]["hit_count"] == 0
    nodes.append(node("5", "0.01"))
    result = worker.execute(original, "published", MAPPING, START, END)
    assert original == before  # No runtime window enters the reviewed artifact.
    assert result["counts"] == {
        "scanned": 3,
        "within_window": 3,
        "matched_transactions": 3,
        "hit_count": 1,
    }
    assert result["hits"][0]["total"] == {"value": 31, "unit": "CNY_MINOR"}
    assert {item["id"] for item in result["hits"][0]["transactions"]} == {"1", "2", "5"}
    assert result["truncation"] == {"scope": False, "hits": False, "evidence": False}


def test_day_grouping_uses_plus_eight_and_half_open_window():
    worker = executor(
        [
            node("1", timestamp="2026-08-31T16:00:00Z"),
            node("2", timestamp="2026-09-01T00:01:00+08:00"),
            node("3", timestamp=END),
            node("4", timestamp="2026-08-31T15:59:59Z"),
        ]
    )
    result = worker.execute(rule(), "published", MAPPING, START, END)
    assert result["counts"]["within_window"] == 2
    assert result["hits"][0]["day"] == "2026-09-01"
    assert result["hits"][0]["total"]["value"] == 40


def test_complete_pagination_and_display_limits_do_not_limit_aggregation():
    worker = executor([node(f"{i:04}", "0.01") for i in range(241)])
    worker.PAGE_SIZE, worker.EVIDENCE_PER_HIT = 31, 3
    result = worker.execute(
        rule("bo_scoped_aggregate", total_min={"value": 240, "unit": "CNY_MINOR"}),
        "published",
        MAPPING,
        START,
        END,
    )
    assert result["counts"]["scanned"] == 241
    assert result["hits"][0]["count"] == 241
    assert result["hits"][0]["total"]["value"] == 241
    assert len(result["hits"][0]["transactions"]) == 3
    assert result["truncation"]["evidence"] is True
    assert len(worker.graph.browser.queries) == 9


def test_scope_overflow_fails_before_returning_or_paging_partial_rows():
    worker = executor([node("1"), node("2"), node("3")])
    worker.MAX_SCOPE_ROWS = 2
    with pytest.raises(AlignmentError, match="超过完整计算上限"):
        worker.execute(rule(), "published", MAPPING, START, END)
    assert len(worker.graph.browser.queries) == 1
    worker.MAX_SCOPE_ROWS, worker.MAX_SCOPE_BYTES = 100, 1
    with pytest.raises(AlignmentError, match="超过完整读取预算"):
        worker.execute(rule(), "published", MAPPING, START, END)


def test_region_and_scoped_aggregate_use_explicit_filters_and_include_descendants():
    worker = executor(
        [
            node("1", "10", txn_type="转出"),
            node("2", "20", txn_type="转出", concept="大额现金存入"),
            node("3", "50", txn_type="转出", region="地区乙"),
            node("4", "900", status="失败"),
        ]
    )
    result = worker.execute(
        rule("counterparty_region", transaction_type="转出"), "published", MAPPING, START, END
    )
    assert {h["id"] for h in result["hits"]} == {"1", "2"}
    result = worker.execute(
        rule(
            "bo_scoped_aggregate",
            txn_type="转出",
            amount_min={"value": 1500, "unit": "CNY_MINOR"},
            total_min={"value": 7000, "unit": "CNY_MINOR"},
        ),
        "published",
        MAPPING,
        START,
        END,
    )
    assert result["hits"][0]["count"] == 2
    assert result["hits"][0]["total"]["value"] == 7000


@pytest.mark.parametrize(
    "amount",
    ["nan", "Infinity", "0.001", "0.100000000000000000000000000001", "9,000", "10000000000000001"],
)
def test_invalid_or_imprecise_amounts_fail_instead_of_silently_skipping(amount):
    with pytest.raises(AlignmentError, match="金额必须"):
        executor([node("1", amount)]).execute(rule(), "published", MAPPING, START, END)


def test_missing_explicit_mapping_and_row_fields_fail_closed():
    worker = executor([node("1")])
    with pytest.raises(AlignmentError, match="显式映射"):
        worker.execute(rule(), "published", {"amount": "金额元"}, START, END)
    assert not worker.graph.browser.queries
    wrong = {**MAPPING, "amount": "guess"}
    with pytest.raises(AlignmentError, match="缺少有效字段 amount"):
        worker.execute(rule(), "published", wrong, START, END)
    with pytest.raises(AlignmentError, match="尚未发布"):
        worker.execute(rule(), "building", MAPPING, START, END)


@pytest.mark.parametrize(
    "start,end",
    [("2026-09-01", END), (START, START), (END, START), (START, "2028-01-01T00:00:00Z")],
)
def test_window_requires_timezone_order_and_budget(start, end):
    with pytest.raises(AlignmentError, match="查询时间"):
        executor([]).execute(rule(), "published", MAPPING, start, end)


def test_naive_source_timestamps_are_never_assumed_to_be_local():
    with pytest.raises(AlignmentError, match="occurred_at"):
        executor([node("1", timestamp="2026-09-01T09:00:00")]).execute(
            rule(), "published", MAPPING, START, END
        )


def test_field_inspection_reports_sample_truncation_without_inferring_mapping():
    worker = executor([node("1"), node("2"), node("3")])
    worker.FIELD_SAMPLE_ROWS = 2
    result = worker.fields("published", ["现金存入"])
    assert result["truncated"] and result["sampled_count"] == 2 and result["total_count"] == 3
    assert {field["name"] for field in result["fields"]} == set(MAPPING.values())
    assert "field_mapping" not in result


def test_display_hit_limit_has_explicit_complete_hit_count():
    worker = executor([node(str(i), account=str(i)) for i in range(4)])
    worker.MAX_HITS = 2
    result = worker.execute(rule("counterparty_region"), "published", MAPPING, START, END)
    assert len(result["hits"]) == 2 and result["counts"]["hit_count"] == 4
    assert result["truncation"]["hits"] is True


def test_compiler_does_not_invent_business_defaults_and_metadata_is_schema():
    original = rule("counterparty_region")
    normalized_original = RuleCompiler().validate(original)
    assert set(normalized_original["body"][0]["params"]) == set(original["body"][0]["params"])
    assert "transaction_type" not in normalized_original["body"][0]["params"]
    normalized = RuleCompiler().validate(rule("counterparty_region", regions=["乙", "甲", "乙"]))
    assert normalized["body"][0]["params"]["regions"] == ["乙", "甲"]
    predicates = supported_predicates()
    assert len(predicates) == 3
    assert all(item["parameter_schema"]["type"] == "object" for item in predicates)
    assert all("amount" in item["required_fields"] for item in predicates)


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(query="MATCH (n) RETURN n"),
        lambda r: r["head"].update(extra=True),
        lambda r: r["body"][0]["params"].update(extra=True),
        lambda r: r["body"][0]["params"].update(
            threshold={"value": 1, "unit": "CNY_MINOR", "cypher": "RETURN 1"}
        ),
        lambda r: r["body"][0]["params"].update(n_min=True),
        lambda r: r["body"][0]["params"].update(threshold={"value": 0.1, "unit": "CNY_MINOR"}),
        lambda r: r["body"][0]["params"].update(threshold={"value": 30, "unit": "CNY"}),
        lambda r: r["body"][0]["params"].update(bo_scope=[]),
        lambda r: r["body"][0]["params"].update(subject_dimension="Customer"),
        lambda r: r["head"].update(semantics="intent"),
        lambda r: r["body"][0].update(predicate="fund_cycle"),
    ],
)
def test_compiler_rejects_arbitrary_queries_unknown_parameters_and_unsupported_semantics(change):
    candidate = rule()
    change(candidate)
    with pytest.raises(AlignmentError):
        RuleCompiler().validate(candidate)


def test_binding_requires_verbatim_source_for_every_parameter_and_predicate():
    candidate = rule("counterparty_region")
    binding = {
        "dataset_revision": "rev",
        "source_node_id": "limit",
        "dimension_hash": "sha",
        "why": {"rule": "成功交易的对手位于地区甲应审核"},
        "parameter_sources": {
            key: {"quote": quote}
            for key, quote in [
                ("predicate", "对手位于地区甲"),
                ("regions", "地区甲"),
                ("status_filter", "成功"),
            ]
        },
    }
    assert binding_issues(binding, candidate) == []
    binding["parameter_sources"]["regions"]["quote"] = "地区乙"
    assert {"field": "regions", "reason": "quote_not_in_source"} in binding_issues(
        binding, candidate
    )
    del binding["parameter_sources"]["status_filter"]
    assert {"field": "status_filter", "reason": "parameter_source_missing"} in binding_issues(
        binding, candidate
    )


def test_graph_ontology_revision_must_match_the_reviewed_rule():
    worker = executor([node("1")])
    with pytest.raises(AlignmentError, match="本体版本与规则依据不一致") as exc:
        worker.execute(rule(), "published", MAPPING, START, END, expected_revision="ontology-v2")
    assert exc.value.status == 409
    assert not worker.graph.browser.queries
    with pytest.raises(AlignmentError, match="本体版本与规则依据不一致"):
        worker.fields("published", ["现金存入"], expected_revision="ontology-v2")
    result = worker.execute(
        rule(), "published", MAPPING, START, END, expected_revision="ontology-v1"
    )
    assert result["ontology_revision"] == "ontology-v1"


def test_query_hash_is_canonical_and_binds_mapping_window_and_policies():
    from bank_project.risk.execution import QUERY_POLICY_VERSION

    rows = [node("1"), node("2")]
    for row in rows:
        row["fields"]["另一金额"] = row["fields"]["金额元"]
    worker = executor(rows)
    first = worker.execute(rule(), "published", MAPPING, START, END)
    same = worker.execute(rule(), "published", dict(reversed(list(MAPPING.items()))), START, END)
    other_mapping = worker.execute(
        rule(), "published", {**MAPPING, "amount": "另一金额"}, START, END
    )
    other_window = worker.execute(rule(), "published", MAPPING, START, "2026-09-03T00:00:00+08:00")
    assert first["query_policy_version"] == QUERY_POLICY_VERSION
    assert len(first["query_hash"]) == 64 and first["query_hash"] == same["query_hash"]
    assert len({first["query_hash"], other_mapping["query_hash"], other_window["query_hash"]}) == 3


def test_execution_and_field_mapping_reject_different_ontology_ids(monkeypatch):
    worker = executor([node("1")])
    monkeypatch.setattr(
        worker.graph.browser,
        "version",
        lambda *args: SimpleNamespace(revision="ontology-v1", ontology_id="another-ontology"),
    )
    with pytest.raises(AlignmentError, match="不同本体"):
        worker.execute(
            rule(),
            "published",
            MAPPING,
            START,
            END,
            expected_revision="ontology-v1",
            expected_ontology_id="selected-ontology",
        )
    with pytest.raises(AlignmentError, match="不同本体"):
        worker.fields(
            "published",
            ["现金存入"],
            expected_revision="ontology-v1",
            expected_ontology_id="selected-ontology",
        )
    assert not worker.graph.browser.queries
