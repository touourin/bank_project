"""Real service chaining keeps annotations and source snapshots across graph versions."""

import asyncio

import pytest
from test_knowledge_matching import finish, sample_graph, service

from bank_project.alignment.models import AlignmentError
from bank_project.api.knowledge import MatchDecision
from bank_project.resolution.models import ManualRequest, StartRequest
from bank_project.resolution.service import ResolutionService


def test_matched_graph_resolves_then_rematches_without_losing_raw_facts(tmp_path):
    knowledge = service(tmp_path)
    resolution = ResolutionService(knowledge.settings, knowledge.load_graph)
    knowledge.resolution = resolution
    matched = finish(knowledge)
    matched_ref = f"match:{matched['id']}:{matched['revision']}"
    assert any(item["id"] == matched_ref for item in knowledge.sources())

    async def resolve():
        run = await resolution.start(StartRequest(source_kind="graphrag", source_id=matched_ref))
        await asyncio.gather(*resolution.tasks)
        return resolution.get(run.id)

    run = asyncio.run(resolve())
    assert run.status == "ready", run.error
    assert run.diagnostics["root_source_id"] == "dataset"
    assert all(node["boid"] == "customer" for node in resolution.graph(run.id)["nodes"])
    run = resolution.manual(
        run.id, ManualRequest(node_ids=["a", "b"], canonical_id="a", expected_revision=run.revision)
    )
    resolved_ref = f"resolution:{run.id}:{run.revision}"
    assert any(item["id"] == resolved_ref for item in knowledge.sources())
    resolved = knowledge.load_graph("graphrag", resolved_ref)
    assert resolved["nodes"][0]["boid"] == "customer"
    assert resolved["nodes"][0]["properties"] == sample_graph()["nodes"][0]["properties"]
    assert resolved["edges"][0]["edge_type"] == "OWNS"
    assert [edge["properties"] for edge in resolved["edges"]] == [
        edge["properties"] for edge in sample_graph()["edges"]
    ]
    assert resolved["root_source_id"] == "dataset"

    async def rematch():
        value = await knowledge.start_match("graphrag", resolved_ref)
        await asyncio.gather(*knowledge.tasks)
        return knowledge.store.get(value["id"])

    rematched = asyncio.run(rematch())
    result = knowledge.result_graph(rematched["id"])
    assert result["nodes"][0]["resolution"] == resolved["nodes"][0]["resolution"]
    assert result["nodes"][0]["properties"] == sample_graph()["nodes"][0]["properties"]
    assert len(result["edges"]) == 2

    # Editing the upstream parent cannot retroactively alter a child's frozen evidence.
    knowledge.review(
        matched["id"],
        MatchDecision(
            target="node", target_id="a", boid="account", expected_revision=matched["revision"]
        ),
    )
    with pytest.raises(AlignmentError, match="更新"):
        knowledge.load_graph("graphrag", matched_ref)
    assert resolution.store.original(run.id)["nodes"][0]["boid"] == "customer"
