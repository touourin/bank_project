"""Financial candidate exclusions must preserve plausible identities and review evidence."""

import pytest

from bank_project.resolution.engine.candidates import retrieve
from bank_project.resolution.engine.contracts import (
    Constraint,
    Corpus,
    Identifier,
    Mention,
    ResolverConfig,
)


def mention(mid, name, *, kind="财务指标", aliases=(), description="", identifiers=()):
    return Mention(
        mid,
        name,
        kind,
        "financial-source",
        f"原始财务记录：{name}。",
        description,
        aliases,
        identifiers,
    )


def recall(*mentions, constraints=(), policy="balanced", limit=10):
    corpus = Corpus("financial-candidates", mentions, constraints=constraints)
    before = corpus.to_dict()
    diagnostics = {}
    candidates, overflow = retrieve(
        corpus,
        ResolverConfig(retrieval_policy=policy, candidate_limit=limit),
        diagnostics=diagnostics,
    )
    assert corpus.to_dict() == before
    return candidates, overflow, diagnostics


@pytest.mark.parametrize(
    "left,right",
    [
        ("科大讯飞2025年营业收入", "科大讯飞2025年净利润"),
        ("科大讯飞2025年应收账款", "科大讯飞2025年开发支出"),
        ("科大讯飞2025年担保额度", "科大讯飞2025年担保余额"),
        ("科大讯飞2024年营业收入", "科大讯飞2025年营业收入"),
        ("科大讯飞2025年上半年营业收入", "科大讯飞2025年下半年营业收入"),
        ("科大讯飞2025年第一季度营业收入", "科大讯飞2025年第二季度营业收入"),
        ("科大讯飞2025年6月末应收账款", "科大讯飞2025年9月末应收账款"),
        ("科大讯飞2025年6月15日应收账款", "科大讯飞2025年6月30日应收账款"),
    ],
)
def test_explicit_financial_conflicts_are_removed_in_both_directions(left, right):
    a, b = mention("a", left), mention("b", right)
    # Each case must have been recalled before the new financial check.
    original, _, _ = recall(a, b, policy="original")
    assert original == {"a": ["b"], "b": ["a"]}

    candidates, overflow, diagnostics = recall(a, b)
    assert candidates == {"a": [], "b": []}
    assert overflow == set()
    assert diagnostics["identity_filtered_pairs"] == 1
    assert diagnostics["identity_filter_reasons"]
    assert set(diagnostics["identity_filter_reasons"].values()) == {1}
    assert diagnostics["selected_pairs"] == 0


@pytest.mark.parametrize(
    "left,right",
    [
        ("科大讯飞2025年营业收入", "科大讯飞2025年营收"),
        ("科大讯飞2025年收入", "科大讯飞2025年营收"),
        ("科大讯飞2025年毛利", "科大讯飞2025年毛利润"),
        ("科大讯飞2025年末应收账款", "科大讯飞2025年12月31日应收账款"),
        ("科大讯飞二〇二五年营业收入", "科大讯飞2025年营业收入"),
        ("科大讯飞2024年2月29日应收账款", "科大讯飞2024年2月末应收账款"),
        ("净利润科技公司2025年营业收入", "净利润科技公司2025年营收"),
    ],
)
def test_equivalent_financial_names_and_periods_remain_candidates(left, right):
    candidates, overflow, diagnostics = recall(mention("a", left), mention("b", right))
    assert candidates == {"a": ["b"], "b": ["a"]}
    assert overflow == set()
    assert diagnostics["identity_filtered_pairs"] == 0
    assert diagnostics["identity_filter_reasons"] == {}


def test_conflicts_are_removed_before_cap_so_a_valid_synonym_is_not_crowded_out():
    base = mention("a", "科大讯飞2025年营业收入")
    synonym = mention("z", "科大讯飞2025年营收")
    conflicting = tuple(
        mention(str(year), f"科大讯飞{year}年营业收入") for year in range(2020, 2025)
    )
    original, original_overflow, _ = recall(base, synonym, *conflicting, policy="original", limit=1)
    assert original["a"] != ["z"]
    assert "a" in original_overflow

    candidates, overflow, diagnostics = recall(base, synonym, *conflicting, limit=1)
    assert candidates["a"] == ["z"]
    assert candidates["z"] == ["a"]
    assert all(candidates[item.mention_id] == [] for item in conflicting)
    assert overflow == set()
    assert diagnostics["identity_filtered_pairs"] == 20
    assert diagnostics["identity_filter_reasons"] == {"年份": 20}
    assert diagnostics["selected_pairs"] == 1


@pytest.mark.parametrize(
    "left,right",
    [
        ("科大讯飞营业收入", "科大讯飞2025年营业收入"),
        ("科大讯飞2025年应收账款", "科大讯飞2025年9月末应收账款"),
        ("科大讯飞2025年上半年应收账款", "科大讯飞2025年6月末应收账款"),
        ("科大讯飞2025年上半年末应收账款", "科大讯飞2025年6月末应收账款"),
        ("科大讯飞2024年至2025年营业收入", "科大讯飞2026年营业收入"),
        ("科大讯飞2024/25年营业收入", "科大讯飞2025年营业收入"),
        ("科大讯飞2024年至25年营业收入", "科大讯飞2025年营业收入"),
        ("科大讯飞营业收入2024万元", "科大讯飞2025年营业收入"),
        ("科大讯飞2025年第一、第二季度营业收入", "科大讯飞2025年第三季度营业收入"),
        ("科大讯飞2025年上半年及下半年营业收入", "科大讯飞2025年第一季度营业收入"),
        ("科大讯飞2025年6月及9月末应收账款", "科大讯飞2025年6月末应收账款"),
        ("科大讯飞2025年6月1日及30日应收账款", "科大讯飞2025年6月30日应收账款"),
        ("科大讯飞2025年净利润与营业收入", "科大讯飞2025年开发支出"),
        ("科大讯飞2025年营业收入增长率", "科大讯飞2025年净利润"),
        ("科大讯飞2025年收入占比", "科大讯飞2025年毛利"),
        ("科大讯飞2025年净收入", "科大讯飞2025年净利润"),
    ],
)
def test_missing_or_ambiguous_name_dimensions_do_not_exclude_candidates(left, right):
    candidates, _, diagnostics = recall(mention("a", left), mention("b", right))
    assert candidates == {"a": ["b"], "b": ["a"]}
    assert diagnostics["identity_filtered_pairs"] == 0


def test_descriptions_do_not_supply_conflicting_years_or_metric_kinds():
    candidates, _, diagnostics = recall(
        mention("a", "科大讯飞财务数据", description="2024年全年营业收入"),
        mention("b", "科大讯飞财务数据", description="2025年上半年净利润"),
    )
    assert candidates == {"a": ["b"], "b": ["a"]}
    assert diagnostics["identity_filtered_pairs"] == 0


def test_shared_alias_does_not_override_a_conflict_in_primary_financial_names():
    candidates, _, diagnostics = recall(
        mention("a", "科大讯飞2024年营业收入", aliases=("科大讯飞营收",)),
        mention("b", "科大讯飞2025年营业收入", aliases=("科大讯飞营收",)),
    )
    assert candidates == {"a": [], "b": []}
    assert diagnostics["identity_filtered_pairs"] == 1


@pytest.mark.parametrize("kind", ["公司", "organization", "UNKNOWN", "BFO_FINANCIAL_ITEM"])
def test_financial_filter_requires_both_records_to_have_an_explicit_financial_type(kind):
    candidates, _, diagnostics = recall(
        mention("a", "科大讯飞2024年营业收入", kind=kind, aliases=("财务记录",)),
        mention("b", "科大讯飞2025年净利润", aliases=("财务记录",)),
    )
    assert candidates == {"a": ["b"], "b": ["a"]}
    assert diagnostics["identity_filtered_pairs"] == 0


@pytest.mark.parametrize("kind", ["财务指标", "financialmetric", "metric"])
def test_supported_financial_type_labels_all_enable_the_filter(kind):
    candidates, _, diagnostics = recall(
        mention("a", "科大讯飞2024年营业收入", kind=kind),
        mention("b", "科大讯飞2025年营业收入", kind=kind),
    )
    assert candidates == {"a": [], "b": []}
    assert diagnostics["identity_filtered_pairs"] == 1


@pytest.mark.parametrize("verdict", ["same", "different"])
def test_reviewed_constraints_reach_the_resolver_despite_name_conflicts(verdict):
    candidates, _, diagnostics = recall(
        mention("a", "科大讯飞2024年营业收入"),
        mention("b", "科大讯飞2025年净利润"),
        constraints=(Constraint("a", "b", verdict, "人工核验记录"),),
    )
    assert candidates == {"a": ["b"], "b": ["a"]}
    assert diagnostics["identity_filtered_pairs"] == 0


@pytest.mark.parametrize("verified", [False, True])
def test_shared_identifier_reaches_the_resolver_for_conflict_review(verified):
    identifier = Identifier("financial-record", "same-record", "来源编号", verified)
    candidates, _, diagnostics = recall(
        mention("a", "科大讯飞2024年营业收入", identifiers=(identifier,)),
        mention("b", "科大讯飞2025年净利润", identifiers=(identifier,)),
    )
    assert candidates == {"a": ["b"], "b": ["a"]}
    assert diagnostics["identity_filtered_pairs"] == 0


def test_equal_identifier_values_in_different_namespaces_do_not_bypass_filter():
    candidates, _, diagnostics = recall(
        mention(
            "a",
            "科大讯飞2024年营业收入",
            identifiers=(Identifier("report-a", "001", "来源编号"),),
        ),
        mention(
            "b",
            "科大讯飞2025年营业收入",
            identifiers=(Identifier("report-b", "001", "来源编号"),),
        ),
    )
    assert candidates == {"a": [], "b": []}
    assert diagnostics["identity_filtered_pairs"] == 1
