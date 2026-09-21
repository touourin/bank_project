"""Capture the original pre-aggregation entity-resolution input on GraphRAG 2.5."""

import importlib
import json
from contextlib import contextmanager
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from bank_project.resolution.engine.export import corpus_from_records

from .jobs import _atomic_json


@contextmanager
def capture_records(folder):
    # Indexing uses a dedicated process. These two hooks observe records and
    # delegate to the unmodified native aggregators; they never patch site-packages.
    module = importlib.import_module("graphrag.index.operations.extract_graph.extract_graph")
    original_entities, original_relationships = module._merge_entities, module._merge_relationships
    records = []

    def entities(frames):
        from .service import json_value

        records.extend(row for frame in frames for row in json_value(frame))
        return original_entities(frames)

    def relationships(frames):
        import pandas as pd

        from .service import json_value

        relations = [row for frame in frames for row in json_value(frame)]
        units = json_value(pd.read_parquet(folder / "output/text_units.parquet"))
        corpus = corpus_from_records(units, records, relations, namespace=folder.name)
        corpus = replace(
            corpus,
            metadata={
                **corpus.metadata,
                "source_dataset": folder.name,
                "raw_entities": records,
                "raw_relationships": relations,
                "profile": json.loads((folder / "profile.json").read_text()),
            },
        )
        relative = f"entity_resolution/inputs/{corpus.sha256}.json"
        target = folder / "output" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(target, corpus.to_dict())
        _atomic_json(
            folder / "output/entity_resolution/latest.json",
            {
                "corpus_sha256": corpus.sha256,
                "path": relative,
            },
        )
        return original_relationships(frames)

    module._merge_entities, module._merge_relationships = entities, relationships
    try:
        yield
    finally:
        module._merge_entities, module._merge_relationships = (
            original_entities,
            original_relationships,
        )


def load_records(folder):
    from bank_project.resolution.engine.contracts import Corpus

    from .jobs import _inside

    pointer = json.loads(_inside(folder, "output/entity_resolution/latest.json").read_text())
    corpus = Corpus.from_dict(json.loads(_inside(folder, "output/" + pointer["path"]).read_text()))
    if corpus.sha256 != pointer["corpus_sha256"]:
        raise ValueError("聚合前消歧记录校验失败")
    return corpus


def records_graph(folder, name):
    """Expose every raw record as its own node, with ambiguity retained in metadata."""
    corpus = load_records(folder)
    from bank_project.resolution.engine.contracts import digest

    raw_edges = {
        str(uuid5(NAMESPACE_URL, f"{corpus.namespace}:relation:{digest(row)}:{ordinal}")): row
        for ordinal, row in enumerate(corpus.metadata["raw_relationships"])
    }
    return {
        "id": f"extracted:{folder.name}",
        "name": name + " · 聚合前记录",
        "source_kind": "graphrag",
        "source_id": f"extracted:{folder.name}",
        "root_source_id": folder.name,
        "resolution_corpus": corpus.to_dict(),
        "nodes": [
            {
                "id": item.mention_id,
                "name": item.name,
                "type": item.type,
                "properties": corpus.metadata["raw_entities"][index],
                "source_context": item.context,
                **({"source_span": list(item.source_span)} if item.source_span is not None else {}),
                "source_text_unit_ids": [item.source_id],
            }
            for index, item in enumerate(corpus.mentions)
        ],
        "edges": [
            {
                "id": row["relation_id"],
                "source": row["source_mention_id"],
                "target": row["target_mention_id"],
                "properties": raw_edges[row["relation_id"]],
            }
            for row in corpus.relations
        ],
        "metadata": corpus.metadata,
    }
