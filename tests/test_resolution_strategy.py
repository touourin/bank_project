"""Recall, budget priority and scheduling checks for the optional optimized strategy."""

import asyncio
import json
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace

import httpx
from pydantic import SecretStr

from bank_project.alignment.model_client import JsonModel
from bank_project.resolution.engine.candidates import retrieve
from bank_project.resolution.engine.contracts import Corpus, Mention, ResolverConfig, read_json
from bank_project.resolution.engine.model_judge import LLMJudge
from bank_project.resolution.engine.resolver import resolve_evidence
from bank_project.settings import Settings


def pairs(candidates):
    return {tuple(sorted((a, b))) for a, others in candidates.items() for b in others}


def mention(mid, name, kind="organization", aliases=(), description="通用企业服务介绍"):
    return Mention(mid, name, kind, "doc", f"原始证据：{name}独立实体记录。", description, aliases)


def test_balanced_keeps_all_labeled_positive_pairs_in_original_fixture():
    root = Path(__file__).parent / "fixtures/entity-resolution/demo"
    corpus = Corpus.from_dict(read_json(root / "corpus.json"))
    labels = {
        m["mention_id"]: m["gold_entity_id"] for m in read_json(root / "gold.json")["mentions"]
    }
    positives = {
        tuple(sorted((a, b)))
        for a, b in combinations(labels, 2)
        if labels[a] and labels[a] == labels[b]
    }
    original, _ = retrieve(corpus, ResolverConfig())
    balanced, _ = retrieve(corpus, ResolverConfig(retrieval_policy="balanced", candidate_limit=10))
    assert positives <= pairs(balanced)
    assert len(pairs(balanced)) < len(pairs(original))
    assert set(balanced) == {m.mention_id for m in corpus.mentions}


def test_balanced_drops_generic_description_but_keeps_alias_across_types():
    corpus = Corpus(
        "synthetic",
        (
            mention("a", "星河科技", "organization", ("GALAXY",)),
            mention("b", "许倩", "person"),
            mention("c", "GALAXY", "UNKNOWN"),
            mention("d", "西湖", "location"),
        ),
    )
    before = corpus.to_dict()
    selected, _ = retrieve(corpus, ResolverConfig(retrieval_policy="balanced"))
    assert pairs(selected) == {("a", "c")}
    assert corpus.to_dict() == before


def test_budget_uses_identity_signals_before_record_ids():
    corpus = Corpus(
        "synthetic",
        (
            mention("a", "东方银行"),
            mention("b", "东方公司"),
            mention("y", "星河科技"),
            mention("z", "星河科技"),
        ),
    )
    called = []

    class Judge:
        version = "test"

        async def judge(self, left, right):
            called.append((left.mention_id, right.mention_id))
            return {"verdict": "uncertain", "reason": "Review required"}

    result = asyncio.run(
        resolve_evidence(
            corpus, ResolverConfig(retrieval_policy="balanced", max_model_calls=1), Judge()
        )
    )
    assert called == [("y", "z")]
    assert len(result.memberships) == 4
    assert result.diagnostics["judge_requests"] == 1


def test_continuous_workers_do_not_wait_for_slowest_pair_in_batch():
    async def exercise():
        gate, third_started = asyncio.Event(), asyncio.Event()
        calls = []

        class Judge:
            version = "test"

            async def judge(self, left, right):
                calls.append((left.mention_id, right.mention_id))
                if len(calls) == 1:
                    await gate.wait()
                elif len(calls) >= 3:
                    third_started.set()
                return {"verdict": "uncertain", "reason": "Review required"}

        corpus = Corpus("synthetic", tuple(mention(str(i), "张伟", "person") for i in range(4)))
        task = asyncio.create_task(
            resolve_evidence(corpus, ResolverConfig(concurrency=2, max_model_calls=4), Judge())
        )
        try:
            await asyncio.wait_for(third_started.wait(), 1)
            assert not gate.is_set()
        finally:
            gate.set()
            result = await task
        assert len(calls) == 4
        assert len(result.decisions) == 6
        assert sum(d["reason"] == "MODEL_BUDGET_EXHAUSTED" for d in result.decisions) == 2

    asyncio.run(exercise())


def test_failure_diagnostics_do_not_accept_unsupported_quotes():
    class Model:
        async def completion_async(self, **kwargs):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "verdict": "same",
                        "left_quote": "invented evidence",
                        "right_quote": "invented evidence",
                        "reason": "fake",
                    }
                )
            )

    corpus = Corpus("synthetic", (mention("a", "张伟"), mention("b", "张伟")))
    judge = LLMJudge(Model(), version="test", namespace=corpus.namespace)
    result = asyncio.run(resolve_evidence(corpus, ResolverConfig(model_policy="apply"), judge))
    assert len(result.entities) == 2
    assert result.decisions[0]["error_code"] == "UNSUPPORTED_SOURCE_QUOTE"


def test_model_transport_reuses_externally_owned_pool():
    async def exercise():
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            model = JsonModel(
                Settings(_env_file=None, model_api_key=SecretStr("test-key")), client=client
            )
            assert await model.complete("system", "user", max_tokens=800) == {"ok": True}
            assert await model.complete("system", "user") == {"ok": True}
            assert not client.is_closed
        assert len(requests) == 2

    asyncio.run(exercise())


def test_concise_quotes_reuse_validated_cache_without_loosening_evidence(tmp_path):
    async def exercise():
        left, right = mention("a", "张伟"), mention("b", "张伟")
        calls = []

        class Model:
            async def completion_async(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "verdict": "same",
                            "reason": "合成证据",
                            "left_quote": left.context,
                            "right_quote": right.context,
                        }
                    )
                )

        original = LLMJudge(
            Model(), version="same-model", namespace="n", cache_path=tmp_path / "cache.db"
        )
        expected = await original.judge(left, right)
        original.close()
        optimized = LLMJudge(
            Model(),
            version="same-model",
            namespace="n",
            cache_path=tmp_path / "cache.db",
            concise_quotes=True,
        )
        assert await optimized.judge(left, right) == expected
        assert optimized.cache_hits == 1 and len(calls) == 1
        optimized.close()

    asyncio.run(exercise())


def test_unmapped_database_types_do_not_exclude_name_candidates():
    corpus = Corpus(
        "db",
        (mention("a", "星河科技", "BFO_CUSTOMER"), mention("b", "星河科技公司", "organization")),
    )
    selected, _ = retrieve(corpus, ResolverConfig(retrieval_policy="balanced"))
    assert pairs(selected) == {("a", "b")}
