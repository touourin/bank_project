"""Native GraphRAG 2.5 integration tests; no external model requests or API credentials.

Run with an environment containing the optional GraphRAG extra. The first test
executes real load/chunk/final-document workflows, the others use native parquet
and native search function signatures with deterministic model responses.
"""

import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bank_project.graphrag import GraphRagService, jobs, runtime
from bank_project.graphrag.parsers import parse_uploads
from bank_project.graphrag.worker import _load_canonical_csv, _verify_artifacts


def make_settings(path):
    return SimpleNamespace(
        data_dir=Path(path),
        model_provider="openai_compatible",
        model_name="test-chat",
        model_base_url="http://127.0.0.1:9999/v1",
        model_api_key="test-only-secret-value",
        model_timeout_seconds=30,
        model_max_retries=0,
        alignment_model_concurrency=1,
        graphrag_embedding_model="test-embedding",
        graphrag_chunk_size=1200,
        graphrag_chunk_overlap=100,
    )


@unittest.skipUnless(
    importlib.util.find_spec("graphrag"), "GraphRAG optional runtime not installed"
)
class NativeGraphRagTests(unittest.TestCase):
    def test_language_adaptation_preserves_native_prompt_fields_and_chinese_graph(self):
        from string import Formatter

        from graphrag.cli.initialize import initialize_project_at
        from graphrag.index.operations.extract_graph.graph_extractor import GraphExtractor

        from bank_project.graphrag.prompts import (
            INDEX_LANGUAGE_POLICY,
            INDEX_PROMPTS,
            QUERY_LANGUAGE_POLICY,
            configure_prompt_languages,
        )

        def fields(value):
            return {field for _, field, _, _ in Formatter().parse(value)}

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "adapted"
            baseline = Path(directory) / "native"
            initialize_project_at(baseline, force=True)
            runtime.initialize(folder, make_settings(directory), 1200, 100)
            for path in (folder / "prompts").glob("*.txt"):
                original = (baseline / "prompts" / path.name).read_text()
                adapted = path.read_text()
                policy = (
                    INDEX_LANGUAGE_POLICY if path.stem in INDEX_PROMPTS else QUERY_LANGUAGE_POLICY
                )
                self.assertTrue(adapted.startswith(policy), path.name)
                self.assertNotIn("Return output in English", adapted)
                self.assertEqual(fields(original), fields(adapted), path.name)
            before = {path.name: path.read_text() for path in (folder / "prompts").glob("*.txt")}
            configure_prompt_languages(folder)
            self.assertEqual(
                before,
                {path.name: path.read_text() for path in (folder / "prompts").glob("*.txt")},
            )

            class Model:
                async def achat(self, prompt):
                    assert INDEX_LANGUAGE_POLICY in prompt
                    assert "科大讯飞与合肥示例银行开展合作。" in prompt
                    return SimpleNamespace(
                        output=SimpleNamespace(
                            content='("entity"<|>科大讯飞<|>ORGANIZATION<|>科大讯飞参与合作。)'
                            '##("entity"<|>合肥示例银行<|>ORGANIZATION<|>银行参与合作。)'
                            '##("relationship"<|>科大讯飞<|>合肥示例银行<|>双方开展合作。<|>8)'
                            "<|COMPLETE|>"
                        )
                    )

            extractor = GraphExtractor(
                model_invoker=Model(),
                prompt=(folder / "prompts/extract_graph.txt").read_text(),
                max_gleanings=0,
            )
            result = asyncio.run(
                extractor(["科大讯飞与合肥示例银行开展合作。"], {"entity_types": ["organization"]})
            )
            self.assertEqual(set(result.output.nodes), {"科大讯飞", "合肥示例银行"})
            self.assertEqual(
                result.output.edges["科大讯飞", "合肥示例银行"]["description"], "双方开展合作。"
            )

    def test_compatible_provider_limits_and_worker_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            settings.graphrag_request_timeout_seconds = 420
            service = GraphRagService(settings)
            job = service.upload(b"source", "source.txt")
            folder = service.root / job["key"]
            runtime.initialize(folder, settings, 1200, 100)
            config = runtime.load_config(folder, settings)
            for model in config.models.values():
                self.assertIsNone(model.tokens_per_minute)
                self.assertIsNone(model.requests_per_minute)
                self.assertEqual(model.request_timeout, 420)
            self.assertEqual(
                runtime.worker_environment(settings)["BANK_GRAPHRAG_REQUEST_TIMEOUT_SECONDS"],
                "420",
            )

    def test_native_pydantic_model_config_error_redacts_injected_runtime_key(self):
        import yaml
        from pydantic import ValidationError

        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            service = GraphRagService(settings)
            job = service.upload(b"source", "source.txt")
            folder = service.root / job["key"]
            runtime.initialize(folder, settings, 1200, 100)
            config_path = folder / "settings.yaml"
            config = yaml.safe_load(config_path.read_text())
            config["models"]["default_chat_model"]["max_retries"] = 0
            config_path.write_text(yaml.safe_dump(config))
            with self.assertRaises(runtime.GraphRagError) as caught:
                runtime.load_config(folder, settings)
            self.assertEqual(caught.exception.status, 503)
            self.assertNotIn(settings.model_api_key, str(caught.exception))
            self.assertIsInstance(caught.exception.__cause__, ValidationError)
            # Confirm the protected native error really includes the injected synthetic key.
            self.assertIn(
                settings.model_api_key, json.dumps(caught.exception.__cause__.errors(), default=str)
            )
            self.assertNotIn(settings.model_api_key, config_path.read_text())

    def test_actual_native_input_chunking_retains_document_ids_and_source_metadata(self):
        import pandas as pd
        from graphrag.api import build_index
        from graphrag.config.enums import InputFileType
        from graphrag.index.input.factory import loaders

        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            service = GraphRagService(settings)
            docs = parse_uploads(
                [
                    ("NA.txt", b"Some company works with another."),
                    ("002.txt", b"Another separate document."),
                ]
            )
            key = jobs.create_dataset(service.root, name="native", description="", documents=docs)
            folder = service.root / key
            runtime.initialize(folder, settings, 1200, 100)
            config = runtime.load_config(folder, settings)
            self.assertEqual(config.models["default_chat_model"].api_key, settings.model_api_key)
            self.assertEqual(config.models["default_chat_model"].request_timeout, 600)
            self.assertNotIn(settings.model_api_key, (folder / "settings.yaml").read_text())
            self.assertFalse((folder / ".env").exists())
            self.assertTrue(Path(config.vector_store["default_vector_store"].db_uri).is_absolute())
            config.workflows = [
                "load_input_documents",
                "create_base_text_units",
                "create_final_documents",
            ]
            with patch.dict(loaders, {InputFileType.csv: _load_canonical_csv}):
                results = asyncio.run(build_index(config=config))
            self.assertEqual(len(results), 3)
            self.assertFalse(any(result.errors for result in results))
            documents = pd.read_parquet(folder / "output/documents.parquet")
            chunks = pd.read_parquet(folder / "output/text_units.parquet")
            manifest = jobs._read_json(folder / "manifest.json")
            self.assertEqual(set(documents.id), {doc["id"] for doc in manifest["documents"]})
            self.assertEqual(set(documents.title), {"NA", "002"})
            self.assertEqual(
                set(documents["metadata"].map(lambda item: item["source_name"])),
                {"NA.txt", "002.txt"},
            )
            self.assertEqual({id for ids in chunks.document_ids for id in ids}, set(documents.id))
            self.assertEqual(service.dataset(key)["status"], "ready")

    def _indexed_fixture(self, directory):
        import pandas as pd

        settings = make_settings(directory)
        service = GraphRagService(settings)
        job = service.upload(b"Alpha company partners with Beta company.", "sample.txt")
        key = job["key"]
        folder = service.root / key
        runtime.initialize(folder, settings, 1200, 100)
        output = folder / "output"
        output.mkdir(exist_ok=True)
        entities = [
            {
                "id": "entity-1",
                "title": "Alpha",
                "type": "COMPANY",
                "description": "Original A",
                "text_unit_ids": ["text-1"],
                "custom": {"risk": 2},
            },
            {
                "id": "entity-2",
                "title": "Beta",
                "type": "COMPANY",
                "description": "Original B",
                "text_unit_ids": ["text-1"],
                "custom": {"risk": 3},
            },
        ]
        relationships = [
            {
                "id": "edge-1",
                "source": "Alpha",
                "target": "Beta",
                "description": "original detail",
                "weight": 2.5,
                "text_unit_ids": ["text-1"],
            },
            {
                "id": "edge-2",
                "source": "Beta",
                "target": "Alpha",
                "description": "reverse detail",
                "weight": 3.0,
                "text_unit_ids": ["text-1"],
            },
        ]
        for name, data in {
            "entities": entities,
            "relationships": relationships,
            "communities": [{"id": "community-1", "level": 0}],
            "community_reports": [{"id": "community-1", "level": 0}],
            "text_units": [{"id": "text-1", "text": "source quote"}],
        }.items():
            pd.DataFrame(data).to_parquet(output / f"{name}.parquet")
        jobs._atomic_json(
            folder / "job.json", {**job, "status": "succeeded", "artifacts": {"community_level": 0}}
        )
        return service, key, entities, relationships

    def test_graph_export_keeps_all_node_edge_properties_and_maps_native_titles(self):
        with tempfile.TemporaryDirectory() as directory:
            service, key, entities, relationships = self._indexed_fixture(directory)
            graph = service.graph(key)
            self.assertEqual([node["properties"] for node in graph["nodes"]], entities)
            self.assertEqual([edge["properties"] for edge in graph["edges"]], relationships)
            self.assertEqual(graph["edges"][0]["source"], "entity-1")
            self.assertEqual(graph["edges"][1]["target"], "entity-1")
            self.assertEqual(graph["source_kind"], "graphrag")
            self.assertEqual(graph["nodes"][0]["source_context"], "source quote")
            self.assertEqual(graph["nodes"][0]["source_evidence_status"], "available")
            json.dumps(graph, allow_nan=False)

    def test_graph_rejects_duplicate_source_ids_and_missing_entity_or_edge_evidence(self):
        import pandas as pd

        for damage in (
            "duplicate_source",
            "empty_source",
            "missing_entity_source",
            "missing_edge_source",
        ):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as directory:
                service, key, _, _ = self._indexed_fixture(directory)
                output = service.root / key / "output"
                if damage == "duplicate_source":
                    frame = pd.read_parquet(output / "text_units.parquet")
                    pd.concat([frame, frame], ignore_index=True).to_parquet(
                        output / "text_units.parquet"
                    )
                elif damage == "empty_source":
                    frame = pd.read_parquet(output / "text_units.parquet")
                    frame.at[0, "text"] = ""
                    frame.to_parquet(output / "text_units.parquet")
                else:
                    table = "entities" if damage == "missing_entity_source" else "relationships"
                    frame = pd.read_parquet(output / f"{table}.parquet")
                    frame.at[0, "text_unit_ids"] = ["deleted-source"]
                    frame.to_parquet(output / f"{table}.parquet")
                with self.assertRaises(runtime.GraphRagError) as caught:
                    service.graph(key)
                self.assertEqual(caught.exception.status, 409)

    def test_graph_explicitly_marks_unreferenced_entities_as_missing_source_evidence(self):
        import pandas as pd

        with tempfile.TemporaryDirectory() as directory:
            service, key, _, _ = self._indexed_fixture(directory)
            path = service.root / key / "output/entities.parquet"
            frame = pd.read_parquet(path)
            frame.at[0, "text_unit_ids"] = []
            frame.to_parquet(path)
            node = service.graph(key)["nodes"][0]
            self.assertEqual(node["source_context"], "")
            self.assertEqual(node["source_evidence_status"], "missing")
            self.assertEqual(node["properties"]["text_unit_ids"], [])

    def test_native_query_signatures_and_complete_context_for_all_methods(self):
        import graphrag.api as api
        import pandas as pd

        with tempfile.TemporaryDirectory() as directory:
            service, key, _, _ = self._indexed_fixture(directory)
            for method in ("local", "global", "drift"):
                native = getattr(api, method + "_search")
                with patch.object(api, method + "_search", autospec=True) as search:
                    search.return_value = (
                        "Grounded answer",
                        {"sources": pd.DataFrame([{"id": "text-1", "text": "source quote"}])},
                    )
                    answer = asyncio.run(service.query(key, "What is the relationship?", method))
                    self.assertEqual(answer["answer"], "Grounded answer")
                    self.assertEqual(answer["context"]["sources"][0]["text"], "source quote")
                    self.assertEqual(answer["index_basis"], "original_graphrag_index")
                    self.assertEqual(search.call_args.kwargs["community_level"], 0)
                    self.assertEqual(len(search.call_args.kwargs["entities"]), 2)
                    # autospec validates kwargs against the installed native method signature.
                    self.assertTrue(callable(native))

    def test_invalid_incomplete_native_artifacts_never_pass_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            service, key, _, _ = self._indexed_fixture(directory)
            with self.assertRaises((ValueError, FileNotFoundError)):
                _verify_artifacts(service.root / key)

    def test_query_provider_errors_do_not_expose_runtime_credentials(self):
        import graphrag.api as api

        with tempfile.TemporaryDirectory() as directory:
            service, key, _, _ = self._indexed_fixture(directory)
            with patch.object(
                api,
                "local_search",
                new=AsyncMock(side_effect=RuntimeError("test-only-secret-value")),
            ):
                with self.assertRaises(runtime.GraphRagError) as caught:
                    asyncio.run(service.query(key, "question"))
                self.assertNotIn("test-only-secret-value", str(caught.exception))

    def test_native_global_model_cache_is_isolated_between_dataset_queries(self):
        import graphrag.api as api
        from graphrag.language_model.manager import ModelManager

        from bank_project.graphrag import service as service_module

        with tempfile.TemporaryDirectory() as directory:
            service, key, _, _ = self._indexed_fixture(directory)
            manager = ModelManager()
            manager.chat_models["local_search_chat"] = object()
            manager.embedding_models["local_search_embedding"] = object()
            active = 0
            maximum = 0

            async def search(**kwargs):
                nonlocal active, maximum
                self.assertNotIn("local_search_chat", manager.chat_models)
                self.assertNotIn("local_search_embedding", manager.embedding_models)
                active += 1
                maximum = max(maximum, active)
                manager.chat_models["local_search_chat"] = object()
                manager.embedding_models["local_search_embedding"] = object()
                await asyncio.sleep(0.01)
                active -= 1
                return "answer", {}

            async def queries():
                return await asyncio.gather(service.query(key, "one"), service.query(key, "two"))

            # Keep this test's contended lock bound to its own event loop.
            with (
                patch.object(service_module, "_NATIVE_QUERY_LOCK", asyncio.Lock()),
                patch.object(api, "local_search", side_effect=search),
            ):
                self.assertEqual(len(asyncio.run(queries())), 2)
            self.assertEqual(maximum, 1)
            self.assertNotIn("local_search_chat", manager.chat_models)
            self.assertNotIn("local_search_embedding", manager.embedding_models)


@unittest.skipUnless(
    importlib.util.find_spec("graphrag"), "GraphRAG optional runtime not installed"
)
class NativeArtifactWorkerTests(unittest.TestCase):
    def _complete_artifacts(self, folder):
        import lancedb
        import pandas as pd
        from graphrag.data_model import schemas

        manifest = jobs._read_json(folder / "manifest.json")
        doc = manifest["documents"][0]
        data = {
            "documents": [
                {
                    "id": doc["id"],
                    "title": doc["title"],
                    "text": "source",
                    "metadata": {
                        "source_name": doc["source_name"],
                        "source_sha256": doc["source_sha256"],
                    },
                    "text_unit_ids": ["unit-1"],
                }
            ],
            "text_units": [
                {
                    "id": "unit-1",
                    "text": "source",
                    "document_ids": [doc["id"]],
                    "entity_ids": ["entity-1", "entity-2"],
                    "relationship_ids": ["edge-1"],
                    "covariate_ids": [],
                }
            ],
            "entities": [
                {
                    "id": "entity-1",
                    "title": "Alpha",
                    "type": "ORGANIZATION",
                    "text_unit_ids": ["unit-1"],
                },
                {
                    "id": "entity-2",
                    "title": "Beta",
                    "type": "ORGANIZATION",
                    "text_unit_ids": ["unit-1"],
                },
            ],
            "relationships": [
                {"id": "edge-1", "source": "Alpha", "target": "Beta", "text_unit_ids": ["unit-1"]}
            ],
            "communities": [
                {
                    "id": "community-1",
                    "level": 0,
                    "community": 0,
                    "entity_ids": ["entity-1", "entity-2"],
                    "relationship_ids": ["edge-1"],
                    "text_unit_ids": ["unit-1"],
                    "children": [],
                }
            ],
            "community_reports": [
                {"id": "report-1", "level": 0, "community": 0, "children": [], "findings": []}
            ],
        }
        output = folder / "output"
        output.mkdir(exist_ok=True)
        for name, rows in data.items():
            columns = getattr(schemas, name.upper() + "_FINAL_COLUMNS")
            rows = [{column: row.get(column) for column in columns} for row in rows]
            pd.DataFrame(rows).to_parquet(output / f"{name}.parquet")
        database = lancedb.connect(str(output / "lancedb"))
        for table, kind in {
            "default-entity-description": "entities",
            "default-community-full_content": "community_reports",
            "default-text_unit-text": "text_units",
        }.items():
            database.create_table(
                table,
                data=[
                    {"id": row["id"], "text": "source", "vector": [1.0, 0.0]} for row in data[kind]
                ],
                mode="overwrite",
            )
        return database

    def test_actual_parquet_and_lancedb_completeness_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            service = GraphRagService(make_settings(directory))
            job = service.upload(b"source", "sample.txt")
            folder = service.root / job["key"]
            database = self._complete_artifacts(folder)
            summary = _verify_artifacts(folder)
            self.assertEqual(summary["vectors"]["default-entity-description"], 2)
            self.assertEqual(summary["tables"]["relationships"], 1)
            database.drop_table("default-community-full_content")
            with self.assertRaisesRegex(ValueError, "缺少向量表"):
                _verify_artifacts(folder)

    def test_worker_publishes_only_complete_outputs_and_restores_loader(self):
        import logging
        import os
        import time

        from graphrag.config.enums import InputFileType
        from graphrag.index.input.factory import loaders

        from bank_project.graphrag import worker

        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            service = GraphRagService(settings)
            job = service.upload(b"source", "sample.txt")
            key = job["key"]
            folder = service.root / key
            runtime.initialize(folder, settings, 1200, 100)
            jobs._atomic_json(
                folder / "job.json",
                {
                    **job,
                    "status": "queued",
                    "run_id": "attempt-1",
                    "pid": os.getpid(),
                    "launch_time": time.time(),
                },
            )
            original_loader = loaders[InputFileType.csv]
            original_log_factory = logging.getLogRecordFactory()

            async def complete_pipeline(*, config, callbacks):
                self.assertIs(loaders[InputFileType.csv], _load_canonical_csv)
                self.assertFalse((service.root / "listing.json").exists())
                callbacks[0].pipeline_start(["fixture"])
                callbacks[0].workflow_start("fixture", None)
                self._complete_artifacts(folder)
                callbacks[0].workflow_end("fixture", None)
                callbacks[0].pipeline_end([])
                return [SimpleNamespace(workflow="fixture", errors=[])]

            with (
                patch("bank_project.settings.Settings", return_value=settings),
                patch("graphrag.api.build_index", side_effect=complete_pipeline),
            ):
                self.assertEqual(worker.run_job(service.root, key, "attempt-1"), 0)
                # Completed attempts cannot index or publish for a second time.
                self.assertEqual(worker.run_job(service.root, key, "attempt-1"), 0)
            self.assertIs(loaders[InputFileType.csv], original_loader)
            self.assertIs(logging.getLogRecordFactory(), original_log_factory)
            self.assertEqual(service.dataset(key)["status"], "succeeded")
            self.assertEqual(len(jobs._listing(service.root)), 1)
            self.assertEqual(
                service.dataset(key)["artifacts"]["vectors"]["default-text_unit-text"], 1
            )

    def test_worker_model_failure_does_not_publish_or_expose_credentials(self):
        import os
        import time

        from bank_project.graphrag import worker

        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            service = GraphRagService(settings)
            job = service.upload(b"source", "sample.txt")
            folder = service.root / job["key"]
            runtime.initialize(folder, settings, 1200, 100)
            jobs._atomic_json(
                folder / "job.json",
                {
                    **job,
                    "status": "queued",
                    "run_id": "attempt-1",
                    "pid": os.getpid(),
                    "launch_time": time.time(),
                },
            )
            with (
                patch.dict(os.environ, {"BANK_GRAPHRAG_API_KEY": settings.model_api_key}),
                patch("bank_project.settings.Settings", return_value=settings),
                patch(
                    "graphrag.api.build_index",
                    new=AsyncMock(
                        return_value=[
                            SimpleNamespace(
                                workflow="extract", errors=["failure " + settings.model_api_key]
                            )
                        ]
                    ),
                ),
            ):
                self.assertEqual(worker.run_job(service.root, job["key"], "attempt-1"), 1)
            result = service.dataset(job["key"])
            self.assertEqual(result["status"], "failed")
            self.assertNotIn(settings.model_api_key, result["error"])
            self.assertFalse((service.root / "listing.json").exists())


if __name__ == "__main__":
    unittest.main()
