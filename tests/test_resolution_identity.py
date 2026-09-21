"""Identity regression cases from financial periods, meetings and product models."""

import asyncio
from types import SimpleNamespace

import pytest

from bank_project.resolution.adapter import annotate_identity
from bank_project.resolution.engine.contracts import Corpus, Mention, ResolverConfig
from bank_project.resolution.engine.identity_guard import assess_identity
from bank_project.resolution.engine.resolver import resolve_evidence
from bank_project.resolution.models import Candidate, NodeBrief

CASES = [
    ("科大讯飞第七届董事会第六次会议", "科大讯飞第七届董事会第三次会议", "事件", "different"),
    ("科大讯飞2025年应收账款", "科大讯飞2025年9月末应收账款", "财务指标", "uncertain"),
    ("科大讯飞2025年6月末开发支出", "科大讯飞2025年开发支出", "财务指标", "uncertain"),
    ("英伟达A100芯片", "英伟达H100芯片", "产品或技术", "different"),
    ("科大讯飞2024年度监事会工作报告", "科大讯飞2025年度董事会工作报告", "事件", "different"),
    ("科大讯飞2025年年报", "科大讯飞2026年半年报", "财务指标", "different"),
]


def node(name, kind):
    return SimpleNamespace(name=name, type=kind)


@pytest.mark.parametrize("left,right,kind,verdict", CASES)
def test_screenshot_pairs_override_similarity_and_model(left, right, kind, verdict):
    a = Mention("a", left, kind, "source", "金额21.89亿元", "金额21.89亿元")
    b = Mention("b", right, kind, "source", "金额21.89亿元", "金额21.89亿元")

    class AlwaysSame:
        version = "unsafe-model"

        async def judge(self, *args):
            pytest.fail("Identity checks must happen before model and cache access")

    result = asyncio.run(
        resolve_evidence(
            Corpus("case", (a, b)),
            ResolverConfig(model_policy="apply"),
            AlwaysSame(),
            candidate_data=({"a": ["b"], "b": ["a"]}, set()),
        )
    )
    assert len(result.entities) == 2
    assert result.decisions[0]["verdict"] == verdict
    assert result.decisions[0]["identity_guard"]["block_merge"] is True
    assert result.diagnostics["judge_requests"] == 0


@pytest.mark.parametrize(
    "left,right,kind",
    [
        ("第七届董事会第六次会议", "第7届董事会第6次会议", "事件"),
        ("英伟达A-100芯片", "英伟达Ａ１００芯片", "产品或技术"),
        ("2025年末应收账款", "2025年12月31日应收账款", "财务指标"),
        ("2024年2月29日余额", "2024年2月末余额", "财务指标"),
        ("二〇二五年年报", "2025年年度报告", "事件"),
    ],
)
def test_equivalent_number_notations_do_not_conflict(left, right, kind):
    assert assess_identity(node(left, kind), node(right, kind)) is None


def test_period_and_snapshot_require_evidence_not_automatic_conflict():
    guard = assess_identity(
        node("2025年上半年余额", "财务指标"), node("2025年6月末余额", "财务指标")
    )
    assert guard["verdict"] == "uncertain" and guard["block_merge"]


@pytest.mark.parametrize("kind", ["organization", "person", "公司", "人物"])
def test_years_in_company_or_person_context_are_not_identity_boundaries(kind):
    a = SimpleNamespace(name="同一实体", type=kind, description="2024年度报告")
    b = SimpleNamespace(name="同一实体", type=kind, description="2025年度报告")
    assert assess_identity(a, b) is None


def test_historical_suggestion_is_retained_but_effective_verdict_is_corrected():
    candidate = Candidate(
        id="old",
        node_ids=["a", "b"],
        nodes=[
            NodeBrief(id="a", name="英伟达A100芯片", type="产品或技术"),
            NodeBrief(id="b", name="英伟达H100芯片", type="产品或技术"),
        ],
        score=0.9,
        reasons=["历史模型建议"],
        evidence={"proposal": "same", "verdict": "uncertain"},
    )
    annotate_identity(candidate)
    assert candidate.evidence["proposal"] == "same"
    assert candidate.evidence["effective_verdict"] == "different"
    assert candidate.status == "excluded"


def test_old_missing_period_review_instructions_are_replaced_without_changing_model_evidence():
    old_message = (
        "一侧缺少身份限定（月份、观测时点），需核对原文后人工决定；不能仅凭名称或金额相同合并"
    )
    candidate = Candidate(
        id="old-period",
        node_ids=["a", "b"],
        nodes=[
            NodeBrief(id="a", name="科大讯飞2025年应收账款", type="财务指标"),
            NodeBrief(id="b", name="科大讯飞2025年9月末应收账款", type="财务指标"),
        ],
        score=0.897,
        reasons=[
            "历史模型建议",
            "身份限定不完整，需核对原文，不能仅凭名称或金额相同合并",
            old_message,
        ],
        evidence={
            "proposal": "same",
            "model_reason": "historical rationale",
            "identity_guard": {
                "version": "identity-dimensions-v1",
                "block_merge": False,
                "message": old_message,
            },
        },
    )
    annotate_identity(candidate)
    once = candidate.model_dump()
    annotate_identity(candidate)
    assert candidate.model_dump() == once
    assert candidate.status == "excluded"
    assert candidate.evidence["effective_verdict"] == "uncertain"
    assert candidate.evidence["proposal"] == "same"
    assert candidate.evidence["model_reason"] == "historical rationale"
    assert all("人工决定" not in r and "需核对原文" not in r for r in candidate.reasons)
    assert "暂不合并" in candidate.evidence["identity_guard"]["message"]


def test_group_explanation_prioritizes_explicit_conflict_over_missing_scope():
    candidate = Candidate(
        id="group",
        node_ids=["a", "b", "c"],
        score=0.9,
        reasons=[],
        nodes=[
            NodeBrief(id="a", name="甲公司2025年年报", type="财务指标"),
            NodeBrief(id="b", name="甲公司年报", type="财务指标"),
            NodeBrief(id="c", name="甲公司2026年年报", type="财务指标"),
        ],
    )
    annotate_identity(candidate)
    assert candidate.evidence["effective_verdict"] == "different"
