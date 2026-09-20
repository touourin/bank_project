"""External matching contracts, workload bounds, and review rather than false certainty."""

import asyncio
import json

import httpx
import pytest
from test_alignment import FakeModel, FakeRetriever, analyze, catalog_bytes, source
from test_alignment_transport import transport

from bank_project.alignment.analyzer import Analyzer
from bank_project.alignment.catalog import Catalog
from bank_project.alignment.editing import revise_mapping
from bank_project.alignment.matching import decide
from bank_project.alignment.models import MappingEditRequest
from bank_project.alignment.retrieval import RetrievalFailure, RetrieveClient
from bank_project.alignment.templates import GraphTemplate


def payload(**changes):
    return {
        "dataset_revision": "r1",
        "node_id": "customer",
        "confident": True,
        "match_method": "exact",
        "candidates": [{"node_id": "customer", "score": 0.9}],
        **changes,
    }


async def search(client, query="客户"):
    async with client.session("r1") as session:
        return await session.search(query)


@pytest.mark.parametrize(
    "changes,status",
    [
        ({}, "matched"),
        ({"confident": False}, "review"),
        ({"candidates": [{"node_id": "customer", "score": 0.327}]}, "review"),
        ({"candidates": [{"node_id": "customer", "score": None}]}, "review"),
        ({"node_id": None, "candidates": []}, "unmatched"),
        ({"node_id": None}, "review"),
        ({"dataset_revision": "r2"}, "mismatch"),
        ({"node_id": "invented", "candidates": [{"node_id": "invented", "score": 1}]}, "mismatch"),
        ({"confident": "true"}, "unavailable"),
        ({"node_id": "other"}, "unavailable"),
        ({"candidates": [{"node_id": "customer", "score": True}]}, "unavailable"),
        ({"candidates": [{"node_id": "customer", "score": float("nan")}]}, "unavailable"),
        ({"candidates": [{"node_id": "customer", "score": 1.1}]}, "unavailable"),
        ({"candidates": [{"node_id": "customer", "score": 0.9}] * 2}, "unavailable"),
    ],
)
def test_only_valid_versioned_service_results_can_choose_nodes(monkeypatch, changes, status):
    def handler(request):
        assert json.loads(request.content) == {"query": "客户", "expected_revision": "r1"}
        return httpx.Response(200, content=json.dumps(payload(**changes)))

    transport(monkeypatch, handler)
    result = asyncio.run(search(RetrieveClient("http://retrieve.invalid")))
    match = decide(result, "column", "name", Catalog(catalog_bytes()), 0.75)
    assert match.status == status
    if status in {"mismatch", "unavailable", "unmatched"}:
        assert match.selected is None


@pytest.mark.parametrize(
    "code,expected,requests",
    [
        (404, "unavailable", 1),
        (409, "mismatch", 1),
        (401, "unavailable", 1),
        (503, "unavailable", 2),
    ],
)
def test_http_errors_are_bounded_and_do_not_expose_provider_body(
    monkeypatch, code, expected, requests
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(code, json={"detail": "provider-sensitive-value"})

    transport(monkeypatch, handler)
    result = asyncio.run(search(RetrieveClient("http://retrieve.invalid")))
    assert result.status == expected and len(calls) == requests
    assert "provider-sensitive-value" not in result.model_dump_json()


def test_response_size_timeout_and_redirects_fail_closed(monkeypatch):
    for response in [
        httpx.Response(200, content=b"x" * (1024 * 1024 + 1)),
        httpx.Response(302, headers={"location": "http://other.invalid"}),
    ]:
        original = httpx.AsyncClient
        with monkeypatch.context() as m:
            m.setattr(
                httpx,
                "AsyncClient",
                lambda original=original, response=response, **kwargs: original(
                    transport=httpx.MockTransport(lambda request: response), **kwargs
                ),
            )
            assert (
                asyncio.run(search(RetrieveClient("http://retrieve.invalid"))).status
                == "unavailable"
            )

    async def slow(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json=payload())

    transport(monkeypatch, slow)
    assert (
        asyncio.run(search(RetrieveClient("http://retrieve.invalid", timeout=0.01))).status
        == "unavailable"
    )


def test_duplicate_queries_reuse_only_successful_results_and_concurrency_is_bounded(monkeypatch):
    active = maximum = 0
    calls = []

    async def handler(request):
        nonlocal active, maximum
        q = json.loads(request.content)["query"]
        calls.append(q)
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json=payload())

    transport(monkeypatch, handler)

    async def scenario():
        client = RetrieveClient("http://retrieve.invalid", concurrency=2)
        async with client.session("r1") as session:
            first = await session.search_many(["客户", "客户", "账户", "年龄", "名称"])
            first[0].response.candidates[0].score = 0
            again = await session.search_many(["客户", "账户"])
            assert again[0].response.candidates[0].score == 0.9
        async with client.session("r2") as session:
            # A new version must never reuse cached evidence from a previous run.
            await session.search_many(["客户"])

    asyncio.run(scenario())
    assert maximum == 2 and calls.count("客户") == 2 and len(calls) == 5


def test_revision_preflight_rejects_mismatch_before_model_call(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(200, json={"dataset_revision": "r2"}))
    with pytest.raises(RetrievalFailure):
        asyncio.run(RetrieveClient("http://retrieve.invalid").check_revision("r1"))


def test_field_nodes_come_from_service_and_low_scores_do_not_become_property_matches():
    class Fields(FakeRetriever):
        async def search_many(self, queries):
            result = await super().search_many(queries)
            for r in result[1:]:
                r.response.node_id = "account"
                data = r.response.model_dump()
                data["candidates"] = [{"node_id": "account", "score": 0.3}]
                r.response = type(r.response).model_validate(data)
            return result

    result = analyze(retriever=Fields())
    assert result.tables[0].concept_id == "customer"
    assert all(c.concept_id is None for c in result.tables[0].columns)
    assert all(m.status == "review" for m in result.tables[0].trace.retrievals[1:])


def test_partial_entity_matching_keeps_all_source_fields_in_default_template():
    model = FakeModel(
        entities=[
            {"id": "customer", "query": "客户", "properties": [{"column": "name", "name": "姓名"}]},
            {"id": "unknown", "query": "未知", "properties": [{"column": "id", "name": "编号"}]},
        ]
    )

    async def progress(message):
        pass

    result = asyncio.run(
        Analyzer(model, FakeRetriever(), relation_index=object()).analyze(
            [source()], Catalog(catalog_bytes()), progress
        )
    )
    assert len(result.template.nodes) == 1
    assert {p.column for p in result.template.nodes[0].properties} == {"id", "name"}
    assert not result.template.confirmed
    assert any("包含多个对象" in note for note in result.tables[0].structure_notes)
    assert any("分组匹配不完整" in note for note in result.tables[0].structure_notes)


def test_single_object_defaults_keep_source_keys_and_fields_instead_of_model_identity():
    s = source()
    s.table.columns[0].primary_key = True
    model = FakeModel(
        entities=[
            {
                "id": "single",
                "query": "客户",
                "key_columns": ["name"],
                "properties": [
                    {"column": "id", "name": "编号"},
                    {"column": "name", "name": "姓名"},
                ],
            }
        ]
    )

    async def progress(message):
        pass

    result = asyncio.run(
        Analyzer(model, FakeRetriever(), relation_index=object()).analyze(
            [s], Catalog(catalog_bytes()), progress
        )
    )
    node = result.template.nodes[0]
    assert node.id == s.table.id and node.identity_scope == s.table.id
    assert node.key_columns == ["id"]
    assert [p.name for p in node.properties] == ["id", "name"]
    assert not result.tables[0].structure_notes
    assert not result.template.confirmed


def test_manual_approval_of_unmatched_table_adds_a_reviewable_template():
    original = analyze(retriever=FakeRetriever(score=0.3))
    original.template = GraphTemplate()
    result = revise_mapping(
        original,
        MappingEditRequest(table_id="customers", concept_id="account", reason="已核对"),
        Catalog(catalog_bytes()),
        [source()],
    )
    assert result.tables[0].status == "mapped"
    assert result.tables[0].trace == original.tables[0].trace
    assert len(result.template.nodes) == 1 and not result.template.confirmed


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/a",
        "http://user:password@host/v2/ontology",
        "http://host/retrieve",
        "http://host/a?key=secret",
        "http://",
    ],
)
def test_bad_retrieve_configuration_is_rejected(url):
    from pydantic import ValidationError

    from bank_project.settings import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, retrieve_base_url=url)


def test_explicit_chinese_names_and_source_comments_are_not_rewritten_by_model():
    from bank_project.alignment.planning import retrieval_query

    assert retrieval_query("客户名称", "对公客户企业全称") == "客户名称"
    assert retrieval_query("cust_id", "人员记录主键", "客户编号") == "客户编号"
    assert retrieval_query("cust_id", "客户编号") == "客户编号"
    assert retrieval_query("crm_cust", "客户资料") == "客户资料"
    assert retrieval_query("交易流水", "金融交易详情记录") == "交易流水"
