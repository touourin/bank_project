"""Migration regressions against source profiles, pre-merge records and paired artifacts."""

import asyncio
import copy
import hashlib
import importlib
import json
from pathlib import Path

import pandas as pd
import pytest
from test_graphrag_pipeline import make_settings
from test_resolution import source

from bank_project.graphrag import runtime
from bank_project.graphrag.exploration import (
    _entity_lookup,
    _ranked_relationships,
    answer_evidence,
    build_core_subgraph,
    build_ego_subgraph,
)
from bank_project.graphrag.profiles import ENTERPRISE_TYPES
from bank_project.graphrag.records import capture_records, load_records, records_graph
from bank_project.graphrag.service import GraphRagService
from bank_project.resolution.engine.contracts import Corpus, ResolverConfig, read_json
from bank_project.resolution.engine.runner import compare_methods
from bank_project.resolution.experiments import load_experiment
from bank_project.resolution.models import DecisionRequest, StartRequest
from bank_project.resolution.service import CompletionAdapter, ResolutionService
from bank_project.settings import Settings


def test_enterprise_profile_routes_and_exact_original_prompts(tmp_path):
    settings = make_settings(tmp_path)
    runtime.initialize(tmp_path / "index", settings, 1000, 150, profile="enterprise_zh")
    cfg = runtime.load_config(tmp_path / "index", settings)
    assert cfg.extract_graph.entity_types == ENTERPRISE_TYPES
    assert cfg.chunks.size == 1000 and cfg.chunks.overlap == 150
    assert (
        cfg.summarize_descriptions.model_id == cfg.community_reports.model_id == "fast_chat_model"
    )
    assert cfg.drift_search.n_depth == 1
    assert cfg.models["default_chat_model"].concurrent_requests == 8
    prompts = Path(__file__).parents[1] / "src/bank_project/graphrag/profiles/enterprise_zh"
    expected = read_json(
        Path(__file__).parent / "fixtures/entity-resolution/enterprise-prompts.sha256.json"
    )
    assert {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in prompts.glob("*.txt")
    } == expected
    for prompt in prompts.glob("*.txt"):
        assert (
            hashlib.sha256((tmp_path / "index/prompts" / prompt.name).read_bytes()).hexdigest()
            == expected[prompt.name]
        )
    assert "test-only-secret-value" not in (tmp_path / "index/settings.yaml").read_text()


def raw_records(tmp_path):
    folder = tmp_path / "dataset"
    (folder / "output").mkdir(parents=True)
    (folder / "profile.json").write_text("{}")
    pd.DataFrame(
        [{"id": "t1", "text": "张伟在上海任职。"}, {"id": "t2", "text": "另一个张伟在北京任职。"}]
    ).to_parquet(folder / "output/text_units.parquet")
    entities = [
        {
            "title": "张伟",
            "type": "人物",
            "description": "任职人员",
            "source_id": "t1",
            "custom": "000123",
        },
        {
            "title": "张伟",
            "type": "人物",
            "description": "另一位人员",
            "source_id": "t2",
            "custom": None,
        },
        {"title": "上海", "type": "地点", "description": "城市", "source_id": "t1", "custom": 0},
    ]
    relationships = [
        {
            "source": "张伟",
            "target": "上海",
            "description": "工作地",
            "weight": 9,
            "source_id": "t1",
        }
    ]
    module = importlib.import_module("graphrag.index.operations.extract_graph.extract_graph")
    original = module._merge_entities
    expected = original([pd.DataFrame(entities)])
    with capture_records(folder):
        actual = module._merge_entities([pd.DataFrame(entities)])
        module._merge_relationships([pd.DataFrame(relationships)])
    pd.testing.assert_frame_equal(actual, expected)
    assert module._merge_entities is original
    return folder, entities, relationships


def test_preaggregation_preserves_duplicate_names_and_all_original_properties(tmp_path):
    folder, entities, relationships = raw_records(tmp_path)
    corpus = load_records(folder)
    assert len(corpus.mentions) == 3
    assert len({m.mention_id for m in corpus.mentions}) == 3
    graph = records_graph(folder, "source")
    assert [n["properties"] for n in graph["nodes"]] == entities
    assert graph["edges"][0]["properties"] == relationships[0]
    assert graph["root_source_id"] == "dataset"
    pointer = json.loads((folder / "output/entity_resolution/latest.json").read_text())
    path = folder / "output" / pointer["path"]
    data = json.loads(path.read_text())
    data["mentions"][0]["name"] = "tampered"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="校验失败"):
        load_records(folder)


def test_original_paired_runner_gold_and_tamper_detection(tmp_path):
    root = Path(__file__).parent / "fixtures/entity-resolution/demo"
    corpus = Corpus.from_dict(read_json(root / "corpus.json"))
    config = ResolverConfig(**read_json(root / "rules.json"))
    report = asyncio.run(
        compare_methods(
            corpus, tmp_path / "paired", config=config, gold=read_json(root / "gold.json")
        )
    )
    experiment = load_experiment(tmp_path, "paired")
    assert report["evaluation_status"] == "MEASURED_ON_PROVIDED_GOLD"
    assert experiment.report["records"] == 11
    assert experiment.report["entity_counts"] == {"legacy_title_v1": 7, "evidence_v1": 7}
    assert len(experiment.results["evidence_v1"]["memberships"]) == 11
    path = tmp_path / "paired/evidence_v1/result.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="校验失败"):
        load_experiment(tmp_path, "paired")


def test_apply_policy_uses_engine_partition_and_can_be_undone(tmp_path):
    async def exercise():
        graph = source()
        service = ResolutionService(
            Settings(_env_file=None, data_dir=tmp_path), lambda *args: copy.deepcopy(graph)
        )

        class Model:
            configured = True

            async def complete(self, system, user):
                value = json.loads(user)
                same = {value[side]["mention_id"] for side in ("left", "right")} == {"a", "b"}
                return {
                    "verdict": "same" if same else "different",
                    "reason": "fixture identity",
                    "left_quote": value["left"]["context"],
                    "right_quote": value["right"]["context"],
                }

        service.model = Model()
        run = await service.start(
            StartRequest(
                source_kind="graphrag",
                source_id="source-version",
                options={"model_policy": "apply", "unique_id_namespaces": ["cust_id"]},
            )
        )
        await asyncio.gather(*service.tasks)
        run = service.get(run.id)
        assert run.status == "ready", run.error
        assert run.summary.node_count == 2 and run.summary.merged_count == 1
        assert service.store.original(run.id) == graph
        merged = next(c for c in run.candidates if c.status == "merged")
        reset = service.decide(
            run.id,
            DecisionRequest(candidate_id=merged.id, action="reset", expected_revision=run.revision),
        )
        assert reset.summary.node_count == 3
        assert load_experiment(service.experiments_root, run.id).report["records"] == 3

    asyncio.run(exercise())


def tables():
    entities = pd.DataFrame(
        [
            {"id": "a", "title": "甲", "type": "公司", "degree": 2},
            {"id": "b", "title": "乙", "type": "公司", "degree": 1},
            {"id": "c", "title": "丙", "type": "人物", "degree": 1},
        ]
    )
    rels = pd.DataFrame(
        [
            {
                "id": "ab",
                "human_readable_id": 0,
                "source": "甲",
                "target": "乙",
                "weight": 8,
                "combined_degree": 3,
            },
            {
                "id": "ac",
                "human_readable_id": 1,
                "source": "甲",
                "target": "丙",
                "weight": 1,
                "combined_degree": 3,
            },
        ]
    )
    return dict(
        entities=entities,
        relationships=rels,
        communities=pd.DataFrame([{"community": 5, "entity_ids": ["a", "b"]}]),
        community_reports=pd.DataFrame([{"community": 5, "human_readable_id": 7}]),
        text_units=pd.DataFrame(),
    )


def test_original_neighbor_rank_and_global_citation_mapping():
    data = tables()
    lookup = _entity_lookup(data["entities"])
    rels = _ranked_relationships(data["relationships"])
    assert build_ego_subgraph(lookup, rels, "甲", 1, 2, None)[0] == ["甲", "乙"]
    assert build_core_subgraph(lookup, rels, 2, ["公司"])[0] == ["甲", "乙"]
    evidence = answer_evidence(
        data,
        "证据 [Data: Reports (7); Relationships (0)]",
        {"reports": [{"id": 7, "in_context": True}]},
    )
    assert set(evidence["node_ids"]) == {"a", "b"}
    assert set(evidence["cited_node_ids"]) == {"a", "b"}
    assert evidence["cited_edge_ids"] == ["ab"]


def test_native_stream_yields_before_completion_and_keeps_context(tmp_path, monkeypatch):
    import graphrag.api as api

    service = GraphRagService(make_settings(tmp_path))
    data = tables()
    data["covariates"] = None
    monkeypatch.setattr(service, "_query_inputs", lambda key: (None, data, 0))

    async def native(**kwargs):
        kwargs["callbacks"][0].on_context(
            {"entities": [{"id": 0, "entity": "甲", "in_context": True}]}
        )
        yield "甲"
        yield "公司 [Data: Entities (0)]"

    monkeypatch.setattr(api, "local_search_streaming", native)

    async def exercise():
        stream = service.query_stream("dataset", "甲是谁")
        assert (await anext(stream))["event"] == "status"
        assert (await anext(stream)) == {"event": "token", "text": "甲"}
        rest = [event async for event in stream]
        assert rest[-1]["result"]["answer"].startswith("甲公司")
        assert rest[-1]["result"]["evidence"]["cited_node_ids"] == ["a"]

    asyncio.run(exercise())


def test_completion_adapter_preserves_step_output_budget(tmp_path, monkeypatch):
    from bank_project.alignment.model_client import JsonModel

    model = JsonModel(Settings(_env_file=None, data_dir=tmp_path))
    calls = []

    async def complete(*args, **kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(model, "complete", complete)
    asyncio.run(
        CompletionAdapter(model).completion_async(
            messages=[{"content": "system"}, {"content": "input"}], max_completion_tokens=800
        )
    )
    assert calls == [{"max_tokens": 800}]


def test_basic_sources_map_back_to_entities_without_inventing_citations():
    data = tables()
    data["text_units"] = pd.DataFrame(
        [{"id": "chunk", "human_readable_id": 2, "entity_ids": ["a", "b"]}]
    )
    evidence = answer_evidence(
        data, "来源 [Data: Sources (2)]", {"Sources": [{"source_id": "2", "text": "甲和乙合作"}]}
    )
    assert set(evidence["node_ids"]) == {"a", "b"}
    assert set(evidence["cited_node_ids"]) == {"a", "b"}
    assert not answer_evidence(
        data, "", {"entities": [{"entity": "甲", "id": "0", "in_context": False}]}
    )["node_ids"]


def test_cli_model_adapter_handles_native_string_model_type(tmp_path, monkeypatch):
    from bank_project.resolution.engine.model_judge import from_project

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        model_provider="openai_compatible",
        model_name="test-chat",
        model_base_url="http://127.0.0.1:9999/v1",
        model_api_key="test-only-cli-key",
        graphrag_embedding_model="test-embedding",
    )
    monkeypatch.setattr("bank_project.settings.Settings", lambda: settings)
    folder = tmp_path / "index"
    runtime.initialize(folder, settings, 1000, 150)
    (folder / "manifest.json").write_text("{}")
    judge = from_project(
        folder,
        "default_chat_model",
        tmp_path / "judge.sqlite",
        "fixture",
        "test-v1",
        legacy_config=True,
    )
    assert judge.model.model.settings.model_name == "test-chat"
    assert judge.model.model.configured
    judge.close()
