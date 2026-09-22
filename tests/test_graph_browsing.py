"""Version-pinned browsing against a separate disposable graph, plus display contracts."""

import os
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bank_project.alignment.browsing import GraphBrowser
from bank_project.alignment.display import display_name, readable_node
from bank_project.alignment.graph import VersionedGraph
from bank_project.alignment.models import AlignmentError, GraphNode, GraphSummary
from bank_project.main import create_app
from bank_project.settings import Settings


def test_labels_prefer_subject_name_never_a_participant_or_internal_table_id():
    assert (
        display_name({"cust_nm": "合成甲公司", "legal_rep_nm": "某某"}, "客户", 8) == "合成甲公司"
    )
    assert display_name({"legal_rep_name": "某某"}, "客户", 8) == "客户 · 第 8 行"
    assert (
        display_name({"EVT_TYPE": "YWJC0002", "OCCUR_DT": "20260920"}, "事件", 4)
        == "YWJC0002 · 20260920"
    )
    node = GraphNode(
        id="instance",
        name="table",
        table_id="table",
        table_name="数据",
        source_row=8,
        concept_id="customer",
        concept_name="客户",
        fields={"cust_nm": "合成甲公司"},
    )
    result = readable_node(node)
    assert result.name == "合成甲公司" and result.id == node.id
    assert node.name == "table"


def test_browse_api_retains_auth_and_validates_bounded_parameters(tmp_path):
    settings = Settings(
        _env_file=None, data_dir=tmp_path, api_token="browser-test-token-1234567890"
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/alignment/graph/overview").status_code == 401
        headers = {"Authorization": "Bearer browser-test-token-1234567890"}
        assert client.get("/api/v1/alignment/graph/overview", headers=headers).json() == {
            "summary": None,
            "groups": [],
        }
        version = str(uuid4())
        version_url = f"/api/v1/alignment/graph/{version}/overview"
        assert client.get(version_url).status_code == 401
        assert client.get(version_url, headers=headers).status_code == 503
        assert (
            client.get("/api/v1/alignment/graph/invalid/overview", headers=headers).status_code
            == 422
        )
        for params in (
            {"limit": 201},
            {"limit": 0},
            {"q": "x" * 201},
            {"after": "x" * 201},
            {"focus": "x" * 201},
        ):
            assert (
                client.get(
                    f"/api/v1/alignment/graph/{version}/nodes", params=params, headers=headers
                ).status_code
                == 422
            )


def test_selected_overview_stays_on_requested_version_and_never_reads_instance_properties(
    monkeypatch,
):
    selected, current = str(uuid4()), str(uuid4())
    summaries = {
        version: GraphSummary(
            version=version,
            run_id=str(uuid4()),
            revision="r1",
            node_count=count,
            edge_count=0,
            created_at="2026-09-21T00:00:00Z",
        )
        for version, count in [(selected, 4), (current, 9)]
    }
    browser = GraphBrowser(SimpleNamespace(configured=True))

    @contextmanager
    def session():
        yield object()

    def query(_session, text, **params):
        assert "BankAlignmentState" not in text
        assert "n.data" not in text
        version = params["version"]
        if "BankAlignmentVersion" in text:
            return (
                [{"summary": summaries[version].model_dump_json()}] if version in summaries else []
            )
        return [
            {
                "concept_id": "customer",
                "concept_name": "客户",
                "count": summaries[version].node_count,
            }
        ]

    monkeypatch.setattr(browser, "session", session)
    monkeypatch.setattr(browser, "query", query)
    result = browser.overview(selected)
    assert result.summary.version == selected
    assert result.summary.node_count == result.groups[0].count == 4
    with pytest.raises(AlignmentError) as missing:
        browser.overview(str(uuid4()))
    assert missing.value.status == 404


@pytest.mark.skipif(not os.getenv("BANK_TEST_NEO4J_URI"), reason="requires a disposable Neo4j")
def test_all_rows_are_reachable_search_is_global_and_versions_are_isolated(tmp_path):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        neo4j_uri=os.environ["BANK_TEST_NEO4J_URI"],
        neo4j_password="ignored-by-disposable-server",
    )
    graph = VersionedGraph(settings)
    version, other, unfinished = str(uuid4()), str(uuid4()), str(uuid4())
    summary = GraphSummary(
        version=version,
        run_id=str(uuid4()),
        revision="test",
        node_count=237,
        edge_count=2,
        created_at="2026-09-20T00:00:00Z",
    )
    nodes = [
        GraphNode(
            id=f"{i:05}",
            name="table",
            table_id="table",
            table_name="数据",
            source_row=i + 2,
            concept_id="customer" if i % 2 == 0 else "event",
            concept_name="客户" if i % 2 == 0 else "事件",
            fields={
                "cust_nm": f"合成客户{i:03}",
                "编号": f"C{i:03}",
                "备注": "O'Reilly \"<script>literal</script>" if i == 220 else "",
                "空值": None,
            },
        )
        for i in range(237)
    ]
    with graph._driver() as driver, driver.session() as session:
        assert session.run("MATCH (n) RETURN count(n) AS n").single()["n"] == 0, (
            "Dedicated test database must be empty"
        )
        session.run('CREATE (:BankAlignmentState {name:"current",version:$v})', v=version).consume()
        for v, status in ((version, "ready"), (other, "ready"), (unfinished, "building")):
            session.run(
                "CREATE (:BankAlignmentVersion {id:$v,status:$status,summary:$summary})",
                v=v,
                status=status,
                summary=summary.model_copy(update={"version": v}).model_dump_json(),
            ).consume()
        session.run(
            'UNWIND $rows AS row CREATE (n:BankAlignedInstance {version:$version,id:row.id, data:row.data, concept_id:row.concept,concept_name:row.name,display_name:"table",field_000:row.label,field_001:row.code,field_002:row.note})',
            version=version,
            rows=[
                {
                    "id": n.id,
                    "data": n.model_dump_json(),
                    "concept": n.concept_id,
                    "name": n.concept_name,
                    "label": n.fields["cust_nm"],
                    "code": n.fields["编号"],
                    "note": n.fields["备注"],
                }
                for n in nodes
            ],
        ).consume()
        other_node = nodes[0].model_copy(
            update={"name": "其他版本", "fields": {"cust_nm": "其他版本"}}
        )
        session.run(
            "CREATE (:BankAlignedInstance {version:$version,id:$id,data:$data})",
            version=other,
            id=other_node.id,
            data=other_node.model_dump_json(),
        ).consume()
        session.run(
            'MATCH (a:BankAlignedInstance {version:$v,id:"00000"}),(b:BankAlignedInstance {version:$v,id:"00220"}),(c:BankAlignedInstance {version:$v,id:"00236"}) CREATE (a)-[:BANK_SOURCE_LINK {version:$v,relation_id:"r1",origin:"confirmed",name:"有依据的关联"}]->(b), (a)-[:BANK_SOURCE_LINK {version:$v,relation_id:"r2",origin:"candidate",name:"候选关联"}]->(c)',
            v=version,
        ).consume()
    try:
        browser = graph.browser
        overview = browser.overview()
        assert sum(g.count for g in overview.groups) == 237
        assert {g.concept_id: g.count for g in overview.groups} == {"customer": 119, "event": 118}
        reached = []
        cursor = ""
        while True:
            page = browser.page(version, after=cursor, limit=50)
            assert page.total == 237 and len(page.nodes) <= 50
            assert all("fields" not in n.model_dump() for n in page.nodes)
            reached.extend(n.id for n in page.nodes)
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        assert reached == [n.id for n in nodes] and len(set(reached)) == 237
        customer = browser.page(version, concept="customer", query="c220")
        assert customer.total == 1 and customer.nodes[0].id == "00220"
        assert browser.page(version, query="O'Reilly").total == 1
        assert browser.page(version, query="' OR 1=1 //").total == 0
        assert browser.page(version, concept="missing").total == 0
        linked = browser.page(version, focus="00000", limit=1)
        assert linked.anchor.id == "00000" and linked.total == 2
        assert linked.nodes[0].id == "00220" and linked.edges[0].name == "有依据的关联"
        next_link = browser.page(version, focus="00000", after=linked.next_cursor, limit=1)
        assert next_link.nodes[0].id == "00236" and next_link.edges[0].origin == "candidate"
        assert browser.detail(version, "00220").fields["空值"] is None
        assert browser.detail(version, "00220").fields["编号"] == "C220"
        assert browser.detail(other, "00000").name == "其他版本"
        with pytest.raises(AlignmentError):
            browser.detail(version, "does-not-exist")
        with pytest.raises(AlignmentError):
            browser.page(unfinished)
        # Browsing a pinned version continues even if a newer version is published.
        with graph._driver() as driver, driver.session() as session:
            session.run(
                'MATCH (s:BankAlignmentState {name:"current"}) SET s.version=$v', v=other
            ).consume()
        assert browser.page(version, query="C220").total == 1
    finally:
        with graph._driver() as driver, driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
