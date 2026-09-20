"""The exported result of one step must be the actual input to the next step."""

import asyncio

import pytest
from test_knowledge_matching import service

from bank_project.alignment.models import AlignmentError
from bank_project.api.knowledge import MatchDecision
from bank_project.resolution.models import ManualRequest, StartRequest
from bank_project.resolution.service import ResolutionService


def test_match_then_resolve_then_rematch_preserves_annotations_and_merged_facts(tmp_path):
    knowledge = service(tmp_path)
    resolution = ResolutionService(knowledge.settings, knowledge.load_graph)
    knowledge.resolution = resolution

    async def execute():
        match = await knowledge.start_match("graphrag", "dataset")
        await asyncio.gather(*knowledge.tasks)
        matched_source = next(s for s in knowledge.sources() if s["id"].startswith("match:"))
        assert matched_source["root_source_id"] == "dataset"
        run = await resolution.start(
            StartRequest(source_kind="graphrag", source_id=matched_source["id"])
        )
        await asyncio.gather(*resolution.tasks)
        original = resolution.store.original(run.id)
        assert all(node["boid"] == "customer" for node in original["nodes"])
        assert original["edges"][0]["edge_type"] == "OWNS"
        ready = resolution.get(run.id)
        merged = resolution.manual(
            run.id,
            ManualRequest(node_ids=["a", "b"], canonical_id="a", expected_revision=ready.revision),
        )
        resolved_source = next(s for s in knowledge.sources() if s["id"].startswith("resolution:"))
        assert resolved_source["root_source_id"] == "dataset"
        resolved = knowledge.load_graph("graphrag", resolved_source["id"])
        assert len(resolved["nodes"]) == 1 and len(resolved["edges"]) == 2
        assert all(edge["source"] == edge["target"] == "a" for edge in resolved["edges"])
        again = await knowledge.start_match("graphrag", resolved_source["id"])
        await asyncio.gather(*knowledge.tasks)
        output = knowledge.result_graph(again["id"])
        assert output["nodes"] == resolved["nodes"]
        assert output["edges"] == resolved["edges"]
        assert output["resolution"]["revision"] == merged.revision
        assert output["parent_graph"]["id"] == resolved_source["id"]
        assert knowledge.store.get(again["id"])["root_source_id"] == "dataset"
        # A version selected before someone else's review must not silently change.
        knowledge.review(
            match["id"], MatchDecision(target="node", target_id="a", boid=None, expected_revision=1)
        )
        with pytest.raises(AlignmentError, match="版本已更新"):
            knowledge.load_graph("graphrag", matched_source["id"])
        await resolution.close()
        await knowledge.close()

    asyncio.run(execute())


def test_clearing_annotations_on_derived_input_really_clears_export(tmp_path):
    knowledge = service(tmp_path)

    async def execute():
        first = await knowledge.start_match("graphrag", "dataset")
        await asyncio.gather(*knowledge.tasks)
        second = await knowledge.start_match("graphrag", f"match:{first['id']}:1")
        await asyncio.gather(*knowledge.tasks)
        knowledge.review(
            second["id"],
            MatchDecision(target="node", target_id="a", boid=None, expected_revision=1),
        )
        output = knowledge.result_graph(second["id"])
        assert "boid" not in output["nodes"][0]
        assert "edge_type" not in output["edges"][0]
        original = knowledge.store.get(second["id"])["graph"]
        assert original["nodes"][0]["boid"] == "customer"
        assert output["nodes"][0]["properties"] == original["nodes"][0]["properties"]
        assert output["edges"][0]["properties"] == original["edges"][0]["properties"]

    asyncio.run(execute())


def test_corrupted_source_has_actionable_error(tmp_path):
    knowledge = service(tmp_path)

    def missing(_):
        raise FileNotFoundError("private location")

    knowledge.graphrag.graph = missing
    with pytest.raises(AlignmentError, match="图谱文件或数据格式不完整") as error:
        knowledge.load_graph("graphrag", "dataset")
    assert error.value.status == 503
