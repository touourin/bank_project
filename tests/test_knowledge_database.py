"""Full graph export must include edges crossing UI/server node batches."""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bank_project.alignment.models import AlignmentError
from bank_project.knowledge.database import DatabaseGraphs


class Browser:
    def __init__(self):
        self.queries = []
        self.nodes = [
            {
                "id": f"n{i:04d}",
                "data": json.dumps(
                    {
                        "id": f"n{i:04d}",
                        "name": "原名",
                        "concept_id": "company",
                        "concept_name": "企业",
                        "fields": {"cust_nm": f"客户{i}", "code": "0001"},
                        "extra": {"null": None},
                    }
                ),
                "stored": {"source_row": i, "unknown": "retain"},
            }
            for i in range(1201)
        ]
        self.edges = [
            {
                "id": f"r{i:04d}",
                "source": "n0000",
                "target": f"n{i:04d}",
                "type": "BANK_SOURCE_LINK",
                "properties": {"weight": 0.4, "unknown": ["原值", "001"], "relation_id": "r"},
            }
            for i in range(1, 1201)
        ]
        self.summary = SimpleNamespace(node_count=1201, edge_count=1200, created_at="2026-09-20")

    @contextmanager
    def session(self):
        yield self

    def version(self, session, version):
        return self.summary

    def query(self, session, text, **params):
        self.queries.append(text)
        values = self.edges if "elementId(r)" in text else self.nodes
        size = 1000 if values is self.edges else 500
        return [v for v in values if v["id"] > params["after"]][:size]


def test_exports_complete_version_including_cross_page_links_and_unknown_fields():
    browser = Browser()
    adapter = DatabaseGraphs(SimpleNamespace(browser=browser))
    result = adapter.load(str(uuid4()))
    assert len(result["nodes"]) == 1201
    assert len(result["edges"]) == 1200
    assert result["edges"][-1]["target"] == "n1200"
    assert result["nodes"][-1]["properties"]["extra"] == {"null": None}
    assert result["nodes"][-1]["storage_properties"] == browser.nodes[-1]["stored"]
    assert result["edges"][-1]["properties"] == browser.edges[-1]["properties"]
    assert all("IN $ids" not in query for query in browser.queries)


def test_count_mismatch_refuses_partial_snapshot():
    browser = Browser()
    browser.summary.edge_count += 1
    with pytest.raises(AlignmentError, match="不完整图谱"):
        DatabaseGraphs(SimpleNamespace(browser=browser)).load(str(uuid4()))
