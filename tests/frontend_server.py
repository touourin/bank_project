"""Browser-test backend with temporary storage and no local credentials."""

import atexit
import json
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from test_alignment import FakeRetriever, catalog_bytes

from bank_project.alignment.graph import graph_edges, graph_rows
from bank_project.alignment.models import (
    AlignmentError,
    GraphEdge,
    GraphGroup,
    GraphNode,
    GraphNodeBrief,
    GraphOverview,
    GraphPage,
    GraphPreview,
    GraphSummary,
)
from bank_project.main import create_app
from bank_project.ontology.service import OntologyService
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
        self.browser = self

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

    def overview(self):
        groups = {}
        for node in self.preview.nodes:
            if node.concept_id not in groups:
                groups[node.concept_id] = GraphGroup(
                    concept_id=node.concept_id, concept_name=node.concept_name, count=0
                )
            groups[node.concept_id].count += 1
        return GraphOverview(summary=self.preview.summary, groups=list(groups.values()))

    def detail(self, version, node_id):
        if self.preview.summary and self.preview.summary.version == version:
            for node in self.preview.nodes:
                if node.id == node_id:
                    return node
        raise AlignmentError("实例不存在", 404)

    def page(self, version, *, concept="", query="", after="", limit=100, focus=""):
        related = {
            e.target if e.source == focus else e.source
            for e in self.preview.edges
            if focus in (e.source, e.target)
        }
        matching = sorted(
            (
                n
                for n in self.preview.nodes
                if (not concept or n.concept_id == concept)
                and (not query or query.lower() in str(n.model_dump()).lower())
                and (not focus or n.id in related)
            ),
            key=lambda n: n.id,
        )
        remaining = [n for n in matching if n.id > after]
        nodes = remaining[:limit]
        ids = {n.id for n in nodes} | ({focus} if focus else set())
        return GraphPage(
            nodes=[GraphNodeBrief.model_validate(n.model_dump()) for n in nodes],
            total=len(matching),
            next_cursor=nodes[-1].id if len(remaining) > limit else None,
            anchor=GraphNodeBrief.model_validate(self.detail(version, focus).model_dump())
            if focus
            else None,
            edges=[e for e in self.preview.edges if e.source in ids and e.target in ids],
        )


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
            local = app.state.settings.model_copy(
                update={
                    "ontology_base_url": None,
                    "retrieve_base_url": None,
                    "risk_ontology_base_url": None,
                }
            )
            ontology = OntologyService(local)
            app.state.ontology = ontology
            app.state.alignment.ontology = app.state.knowledge.ontology = (
                app.state.risk.ontology
            ) = ontology
            app.state.alignment.analyzer.model = BrowserModel()
            app.state.alignment.analyzer.retriever = BrowserRetriever()
            app.state.alignment.graph = BrowserGraph()
            yield

    app.router.lifespan_context = lifespan
    return app
