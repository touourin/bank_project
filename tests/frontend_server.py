"""Browser-test backend with temporary storage and no local credentials."""

import atexit
import json
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from test_alignment import FakeRetriever, catalog_bytes

from bank_project.alignment.graph import graph_edges, graph_rows
from bank_project.alignment.models import GraphEdge, GraphNode, GraphPreview, GraphSummary
from bank_project.main import create_app
from bank_project.settings import Settings


class BrowserModel:
    async def complete(self, system, user):
        payload = json.loads(user)
        return {
            "meaning": "客户资料",
            "query": "客户",
            "columns": [
                {
                    "column": c["name"],
                    "query": c["name"],
                    "semantic": "name" if "name" in c["name"] else "id",
                }
                for c in payload["table"]["columns"]
            ],
        }


class BrowserGraph:
    configured = True

    def __init__(self):
        self.preview = GraphPreview()

    def publish(self, run, sources, sequence, check):
        check()
        nodes = [GraphNode.model_validate_json(n["data"]) for n in graph_rows(sources, run.result)]
        edges = [GraphEdge(**e) for e in graph_edges(sources, run.result)]
        summary = GraphSummary(
            version=str(uuid4()),
            run_id=run.id,
            revision="r1",
            node_count=len(nodes),
            edge_count=len(edges),
            created_at=run.created_at,
        )
        self.preview = GraphPreview(summary=summary, nodes=nodes, edges=edges)
        return summary

    def current(self):
        return self.preview


class BrowserRetriever(FakeRetriever):
    async def search_many(self, queries):
        results = await super().search_many(["客户" if q == "对齐客户" else q for q in queries])
        for result, query in zip(results, queries, strict=True):
            result.query = query
        return results


_workspace = TemporaryDirectory(prefix="bank-intake-browser-")
atexit.register(_workspace.cleanup)


def app_factory():
    path = Path(_workspace.name) / "ontology.json"
    path.write_bytes(catalog_bytes())
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=Path(_workspace.name),
            ontology_snapshot=path,
            model_api_key="browser-test-only",
            retrieve_base_url="http://retrieve.invalid",
        )
    )
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original(app):
            app.state.alignment.analyzer.model = BrowserModel()
            app.state.alignment.analyzer.retriever = BrowserRetriever()
            app.state.alignment.graph = BrowserGraph()
            yield

    app.router.lifespan_context = lifespan
    return app
