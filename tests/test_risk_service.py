"""End-to-end WHY mapping, immutable provenance, review CAS and execution gate."""

import asyncio
import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bank_project.alignment.models import AlignmentError
from bank_project.risk.models import ExecutionRequest, PropagationRequest, ReviewRequest
from bank_project.risk.service import RiskService
from bank_project.risk.store import RiskStore
from bank_project.settings import Settings

WHY = [
    {
        "why_id": "cash-limit",
        "priority": "高",
        "text": "同一账户成功交易在+08:00自然日内至少2笔现金存入，累计超过人民币100元且每笔不超过100元。",
    }
]


def snapshot(path, *, why=WHY):
    revision = "r1"
    nodes = [{"label": "OntologyDataset", "properties": {"revision": revision, "status": "ready"}}]
    for key, name in (
        ("limit", "现金存入限额"),
        ("cash", "现金存入"),
        ("large", "大额现金存入"),
        ("neighbor", "相邻业务"),
    ):
        props = {"node_id": key, "node_name": name, "dataset_revision": revision}
        if key == "limit" and why:
            props["why"] = why
        nodes.append({"key": f"Concept:{revision}:{key}", "label": "Concept", "properties": props})
    edges = [
        {
            "start": f"Concept:{revision}:{a}",
            "end": f"Concept:{revision}:{b}",
            "type": kind,
            "properties": {"dataset_revision": revision},
        }
        for a, b, kind in (
            ("limit", "cash", "INHERES_IN"),
            ("large", "cash", "IS_A"),
            ("neighbor", "cash", "ADJACENT_TO"),
        )
    ]
    path.write_text(
        json.dumps({"graph": {"nodes": nodes, "relationships": edges}}, ensure_ascii=False)
    )


def candidate():
    params = {
        "threshold": {"value": 10000, "unit": "CNY_MINOR"},
        "n_min": 2,
        "max_single_le_threshold": True,
        "timezone": "+08:00",
        "subject_dimension": "Account",
        "cash_scope": "现金存入",
        "status_filter": "成功",
    }
    return {
        "name": "现金累计",
        "description": "来源约束",
        "predicate": "cash_aggregate_threshold",
        "params": params,
        "source_node_id": "limit",
        "parameter_sources": {key: {"quote": WHY[0]["text"]} for key in ["predicate", *params]},
    }


class Model:
    def __init__(self, raw=None):
        self.raw = raw or candidate()
        self.calls = []

    async def complete(self, system, user):
        self.calls.append((system, json.loads(user)))
        return {"cases": [copy.deepcopy(self.raw)]}


class Executor:
    def __init__(self):
        self.calls = []

    def execute(self, *args, **kwargs):
        assert kwargs["expected_revision"] == "r1"
        self.calls.append(args)
        return {"hits": [{"id": "hit"}], "counts": {"hit_count": 1}}


def service(tmp_path, *, raw=None, why=WHY):
    path = tmp_path / "snapshot.json"
    snapshot(path, why=why)
    config = Settings(
        _env_file=None, data_dir=tmp_path / "data", ontology_snapshot=path, ontology_revision="r1"
    )
    return RiskService(config, SimpleNamespace(configured=False), model=Model(raw))


async def generate(value):
    job = await value.start(PropagationRequest(anchor_node_ids=["cash"]))
    await asyncio.gather(*value.tasks)
    return value.store.get("jobs", job["id"])


def review(case, **kwargs):
    return ReviewRequest(
        expected_version=case["version"],
        expected_hash=case["content_hash"],
        action="approve",
        evidence_confirmed=True,
        **kwargs,
    )


def execution(case):
    return ExecutionRequest(
        expected_version=case["version"],
        expected_hash=case["content_hash"],
        graph_version=uuid4(),
        field_mapping={"amount": "金额"},
        start="2026-01-01T00:00:00+08:00",
        end="2026-01-02T00:00:00+08:00",
    )


def test_generate_pin_scope_review_execute_and_revoke(tmp_path):
    raw = candidate()
    raw["params"]["bo_scope"] = ["limit", "neighbor"]
    value = service(tmp_path, raw=raw)
    job = asyncio.run(generate(value))
    assert job["status"] == "succeeded", job
    assert len(job["case_ids"]) == 1
    case = value.store.get("cases", job["case_ids"][0])
    assert case["review_status"] == "pending_review" and case["execution_status"] == "blocked"
    assert case["rule_pack"]["body"][0]["params"]["bo_scope"] == ["cash", "large"]
    binding = case["source_binding"]
    assert binding["why"] == WHY and binding["source_node_id"] == "limit"
    assert binding["propagation_path"][0]["relation_type"] == "inheres_in"
    assert binding["propagation"]["path_strength"] == "strong"
    assert len(binding["dimension_hash"]) == 64
    assert value.model.calls[0][1]["why_candidates"][0]["why"] == WHY
    value.executor = Executor()
    with pytest.raises(AlignmentError, match="尚未通过"):
        asyncio.run(value.execute(case["id"], execution(case)))
    assert value.executor.calls == []
    with pytest.raises(AlignmentError, match="核对"):
        value.review(case["id"], review(case).model_copy(update={"evidence_confirmed": False}))
    # Updating the active source file cannot change a saved rule's pinned review source.
    snapshot(value.settings.ontology_snapshot, why=[{"text": "变更后的规则"}])
    approved = value.review(case["id"], review(case))
    assert approved["version"] == 2 and approved["execution_status"] == "ready"
    with pytest.raises(AlignmentError, match="已变化"):
        value.review(case["id"], review(case))
    result = asyncio.run(value.execute(case["id"], execution(approved)))
    assert result["status"] == "succeeded" and result["case_version"] == 2
    assert value.executor.calls[0][0] == case["rule_pack"]
    assert result["source_binding"] == binding
    rejected = value.review(
        case["id"], review(approved).model_copy(update={"action": "reject", "reason": "需要重核"})
    )
    assert rejected["version"] == 3 and rejected["execution_status"] == "blocked"
    with pytest.raises(AlignmentError, match="尚未通过"):
        asyncio.run(value.execute(case["id"], execution(rejected)))
    assert len(value.store.list("executions", case["id"])) == 1


@pytest.mark.parametrize("change", ["bad_quote", "unknown_source", "query", "anchor"])
def test_invalid_mapping_rejected_without_case(tmp_path, change):
    raw = candidate()
    if change == "bad_quote":
        raw["parameter_sources"]["threshold"] = {"quote": "不是原文"}
    elif change == "unknown_source":
        raw["source_node_id"] = "neighbor"
    elif change == "query":
        raw["query"] = "MATCH (n) DELETE n"
    else:
        raw["anchor_node_id"] = "large"
    value = service(tmp_path, raw=raw)
    job = asyncio.run(generate(value))
    assert job["status"] == "succeeded" and job["case_ids"] == []
    assert job["rejections"] and job["coverage"]["complete"] is False


def test_missing_parameter_survives_but_cannot_be_approved(tmp_path):
    raw = candidate()
    del raw["params"]["threshold"]
    value = service(tmp_path, raw=raw)
    job = asyncio.run(generate(value))
    case = value.store.get("cases", job["case_ids"][0])
    assert any(i["field"] == "threshold" for i in case["validation_issues"])
    with pytest.raises(AlignmentError, match="缺少参数"):
        value.review(case["id"], review(case))


def test_empty_why_is_explicit_and_does_not_call_model(tmp_path):
    value = service(tmp_path, why=None)
    job = asyncio.run(generate(value))
    assert job["case_ids"] == [] and value.model.calls == []
    assert job["anchors"][0]["no_why_sources"] is True


def test_pinned_snapshot_tampering_and_store_restart(tmp_path):
    value = service(tmp_path)
    job = asyncio.run(generate(value))
    case = value.store.get("cases", job["case_ids"][0])
    path = value.snapshots / f"{case['source_binding']['snapshot_sha256']}.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(AlignmentError, match="内容已变化"):
        value.review(case["id"], review(case))
    job.update(status="running")
    value.store.put("jobs", job)
    reopened = RiskStore(value.store.path)
    assert reopened.get("jobs", job["id"])["status"] == "failed"
    assert reopened.get("cases", case["id"])["content_hash"] == case["content_hash"]


@pytest.mark.parametrize(
    "policy", ["COMPILER_POLICY_VERSION", "SOURCE_POLICY_VERSION", "QUERY_POLICY_VERSION"]
)
def test_policy_changes_block_reusing_an_approval(tmp_path, monkeypatch, policy):
    import bank_project.risk.service as module

    value = service(tmp_path)
    job = asyncio.run(generate(value))
    case = value.store.get("cases", job["case_ids"][0])
    binding = case["source_binding"]
    assert all(
        binding[key]
        for key in ("compiler_policy_version", "source_policy_version", "query_policy_version")
    )
    approved = value.review(case["id"], review(case))
    value.executor = Executor()
    monkeypatch.setattr(module, policy, "future-policy.v2")
    with pytest.raises(AlignmentError, match="政策版本已变化"):
        asyncio.run(value.execute(case["id"], execution(approved)))
    assert value.executor.calls == [] and value.store.list("executions") == []
    with pytest.raises(AlignmentError, match="政策版本已变化"):
        value.review(case["id"], review(approved))
    # Revoking old approval must remain available even after a policy upgrade.
    rejected = value.review(case["id"], review(approved).model_copy(update={"action": "reject"}))
    assert rejected["execution_status"] == "blocked"


def test_review_race_is_atomic_and_tampering_cannot_bypass_integrity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    value = service(tmp_path)
    job = asyncio.run(generate(value))
    case = value.store.get("cases", job["case_ids"][0])

    def attempt():
        try:
            return value.review(case["id"], review(case))["review_status"]
        except AlignmentError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(map(str, results)) == ["409", "approved"]
    current = value.store.get("cases", case["id"])
    assert len(current["audits"]) == 1
    current["rule_pack"]["body"][0]["params"]["threshold"]["value"] = 1
    value.store.put("cases", current)
    with pytest.raises(AlignmentError, match="完整性校验失败"):
        asyncio.run(value.execute(current["id"], execution(current)))


@pytest.mark.parametrize("phase", ["gate", "evaluation"])
def test_cancelled_execution_is_durably_failed_at_each_async_boundary(tmp_path, monkeypatch, phase):
    from threading import Event

    value = service(tmp_path)
    job = asyncio.run(generate(value))
    case = value.store.get("cases", job["case_ids"][0])
    approved = value.review(case["id"], review(case))
    entered, release = Event(), Event()
    value.executor = Executor()

    if phase == "gate":
        original = value.store.begin_execution

        def blocking(*args):
            entered.set()
            assert release.wait(5)
            return original(*args)

        monkeypatch.setattr(value.store, "begin_execution", blocking)
    else:

        def blocking(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return {"hits": []}

        monkeypatch.setattr(value.executor, "execute", blocking)

    async def cancel():
        task = asyncio.create_task(value.execute(case["id"], execution(approved)))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    records = value.store.list("executions", case["id"])
    assert len(records) == 1 and records[0]["status"] == "failed"
    assert records[0]["result"] is None and "中断" in records[0]["error"]
    assert records[0]["case_version"] == approved["version"]


def test_execution_failure_is_persisted_with_approved_snapshot(tmp_path):
    class BrokenExecutor:
        def execute(self, *args, **kwargs):
            raise AlignmentError("概念作用域超过完整计算上限")

    value = service(tmp_path)
    job = asyncio.run(generate(value))
    case = value.store.get("cases", job["case_ids"][0])
    approved = value.review(case["id"], review(case))
    value.executor = BrokenExecutor()
    result = asyncio.run(value.execute(case["id"], execution(approved)))
    assert result["status"] == "failed" and result["result"] is None
    assert "超过完整计算上限" in result["error"]
    stored = value.store.get("executions", result["id"])
    assert stored["rule_pack"] == approved["rule_pack"]
    value.review(case["id"], review(approved).model_copy(update={"action": "reject"}))
    assert value.store.get("executions", result["id"]) == stored


def test_mapping_preserves_exact_long_raw_column_names(tmp_path):
    raw = " " + "字段" * 200 + " "
    payload = ExecutionRequest(
        expected_version=1,
        expected_hash="a" * 64,
        graph_version=uuid4(),
        field_mapping={"amount": raw},
        start="2026-01-01T00:00:00+08:00",
        end="2026-01-02T00:00:00+08:00",
    )
    assert payload.field_mapping["amount"] == raw


def test_partial_why_import_remains_visible_in_generation_coverage(tmp_path):
    value = service(tmp_path)
    path = value.settings.ontology_snapshot
    raw = json.loads(path.read_bytes())
    raw["risk_why_import"] = {
        "coverage_complete": False,
        "verified_node_count": 1,
        "total_node_count": 4,
    }
    path.write_text(json.dumps(raw, ensure_ascii=False))
    job = asyncio.run(generate(value))
    assert job["case_ids"]
    assert job["coverage"]["complete"] is False
    assert job["coverage"]["why_import"]["verified_node_count"] == 1
