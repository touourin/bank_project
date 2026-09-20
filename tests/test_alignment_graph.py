"""Opt-in tests against a disposable Neo4j, never the configured business database.

Set BANK_TEST_NEO4J_URI to a separate, empty Neo4j with authentication disabled.
"""

import os
from unittest.mock import patch

import pytest
from neo4j.exceptions import ServiceUnavailable
from test_alignment import mapped, source

from bank_project.alignment.graph import VersionedGraph
from bank_project.alignment.models import AlignmentError, MappingResult, RelationProposal, Run
from bank_project.alignment.relations import map_relations
from bank_project.settings import Settings


@pytest.mark.skipif(not os.getenv("BANK_TEST_NEO4J_URI"), reason="requires a disposable Neo4j")
def test_atomic_publication_failure_preservation_and_stale_worker_fencing():
    graph = VersionedGraph(
        Settings(
            _env_file=None,
            neo4j_uri=os.environ["BANK_TEST_NEO4J_URI"],
            neo4j_password="ignored-by-disposable-server",
        )
    )
    left, right = source("left"), source("right")
    sources = [left, right]
    mappings = [mapped(left), mapped(right)]
    proposal = RelationProposal(
        target_table_id="right", source_columns=["id"], target_columns=["id"], reason="候选关联"
    )
    result = MappingResult(
        revision="test",
        snapshot_sha256="test",
        tables=mappings,
        relations=map_relations(sources, mappings, {"left": [proposal]}),
    )
    run = Run(id="disposable-test", created_at="test", status="ready", result=result)
    assert graph.current().summary is None, "Test database must be empty"
    summary = graph.publish(run, sources, 1, lambda: None)
    current = graph.current()
    assert current.summary == summary
    assert len(current.nodes) == 4 and len(current.edges) == 2
    assert all(e.origin == "candidate" for e in current.edges)
    query = graph._query

    def fail_after_some_nodes(session, text, **params):
        if "CREATE (a)-" in text:
            raise ServiceUnavailable("injected after nodes staged")
        return query(session, text, **params)

    with patch.object(graph, "_query", side_effect=fail_after_some_nodes):
        with pytest.raises(AlignmentError):
            graph.publish(run, sources, 2, lambda: None)
    assert graph.current().summary.version == summary.version
    newer = graph.publish(run, sources, 3, lambda: None)
    assert newer.version != summary.version
    with pytest.raises(AlignmentError, match="更新"):
        graph.publish(run, sources, 2, lambda: None)
    assert graph.current().summary.version == newer.version

    def drop_edges(session, text, **params):
        if "CREATE (a)-" in text:
            return []
        return query(session, text, **params)

    with patch.object(graph, "_query", side_effect=drop_edges):
        with pytest.raises(AlignmentError, match="数量不完整"):
            graph.publish(run, sources, 4, lambda: None)
    assert graph.current().summary.version == newer.version

    with graph._driver() as driver, driver.session() as session:
        assert (
            session.run(
                "MATCH (v:BankAlignmentVersion {status:'ready'}) RETURN count(v) AS count"
            ).single()["count"]
            == 2
        )
