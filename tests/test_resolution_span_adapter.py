"""Keep explicit source occurrences through online and pre-aggregation adapters."""

from copy import deepcopy
from dataclasses import replace

import pytest

from bank_project.graphrag.records import records_graph
from bank_project.resolution.adapter import (
    MISSING_SOURCE_CONTEXT,
    corpus_from_graph,
    source_records,
)
from bank_project.resolution.engine.export import corpus_from_records


def node(context="张伟负责芯片。张伟负责销售。", **updates):
    return {
        "id": "person",
        "name": "张伟",
        "type": "person",
        "properties": {"description": "模型生成描述：张伟是一名医生。"},
        "source_context": context,
        **updates,
    }


def graph(item, kind="graphrag"):
    return {"source_kind": kind, "source_id": "document", "nodes": [item], "edges": []}


def test_graph_preserves_explicit_second_occurrence_and_original_text():
    item = node(source_span=[7, 9], evidence_kind="catalog")
    item["properties"]["evidence_kind"] = "catalog"
    before = deepcopy(item)

    mention = corpus_from_graph(graph(item)).mentions[0]

    assert mention.context == item["source_context"]
    assert mention.source_span == (7, 9)
    assert mention.context[slice(*mention.source_span)] == "张伟"
    assert "医生" not in mention.context
    assert mention.evidence_kind == "source"
    assert item == before


def test_graph_does_not_infer_occurrence_from_name_or_generated_properties():
    item = node()
    item["properties"]["source_span"] = [0, 2]
    mention = corpus_from_graph(graph(item)).mentions[0]
    assert mention.source_span is None
    assert mention.context == item["source_context"]

    item.pop("source_context")
    item["source_span"] = [0, 2]
    missing = corpus_from_graph(graph(item)).mentions[0]
    assert missing.context == MISSING_SOURCE_CONTEXT
    assert missing.source_span is None


@pytest.mark.parametrize("span", [[0, 200], [0, True], [2, 4], [0], "0:2"])
def test_graph_rejects_invalid_explicit_occurrence(span):
    with pytest.raises(ValueError, match="source_span"):
        corpus_from_graph(graph(node(source_span=span)))


def test_merged_graph_moves_explicit_offsets_with_unmodified_source_context():
    previous = node(source_span=[7, 9])
    item = node(
        "先前文档没有该人名。",
        resolution={"source_nodes": [previous, deepcopy(previous)]},
    )
    mention = corpus_from_graph(graph(item)).mentions[0]
    prefix = item["source_context"] + "\n\n"
    assert mention.context == prefix + previous["source_context"]
    assert mention.source_span == (len(prefix) + 7, len(prefix) + 9)


def test_merged_graph_retains_distinct_occurrences_without_selecting_one():
    first = node(source_span=[0, 2])
    second = node(source_span=[7, 9])
    first["resolution"] = {"source_nodes": [second]}
    assert len(source_records(first)) == 2
    mention = corpus_from_graph(graph(first)).mentions[0]
    assert mention.context == first["source_context"]
    assert mention.source_span is None


def test_merged_alias_occurrence_does_not_become_canonical_name_span():
    item = node(
        "",
        name="张先生",
        resolution={"source_nodes": [node(source_span=[7, 9])]},
    )
    mention = corpus_from_graph(graph(item)).mentions[0]
    assert "张伟" in mention.aliases
    assert mention.source_span is None


def test_database_graph_keeps_structured_record_evidence():
    item = node(source_span=[7, 9])
    mention = corpus_from_graph(graph(item, kind="database")).mentions[0]
    assert "description" in mention.context and "医生" in mention.context
    assert mention.source_span is None
    assert mention.evidence_kind == "source"


def test_preaggregation_and_graph_roundtrip_preserve_only_supplied_spans(monkeypatch, tmp_path):
    context = "张伟负责芯片。张伟负责销售。"
    entities = [
        {
            "title": "张伟",
            "type": "person",
            "description": "销售人员",
            "source_id": "unit",
            "source_span": [7, 9],
            "evidence_kind": "catalog",
        },
        {"title": "张伟", "type": "person", "description": "未定位人员", "source_id": "unit"},
    ]
    corpus = corpus_from_records([{"id": "unit", "text": context}], entities, [], "example")
    assert corpus.mentions[0].source_span == (7, 9)
    assert corpus.mentions[1].source_span is None
    assert all(item.evidence_kind == "source" for item in corpus.mentions)
    corpus = replace(corpus, metadata={"raw_entities": entities, "raw_relationships": []})
    monkeypatch.setattr("bank_project.graphrag.records.load_records", lambda folder: corpus)

    result = records_graph(tmp_path, "source")
    assert result["nodes"][0]["source_span"] == [7, 9]
    assert "source_span" not in result["nodes"][1]
    roundtrip = corpus_from_graph(result)
    assert [item.source_span for item in roundtrip.mentions] == [(7, 9), None]
    assert all(item.context == context for item in roundtrip.mentions)


def test_preaggregation_validates_offsets_against_original_unit_text():
    with pytest.raises(ValueError, match="source_span"):
        corpus_from_records(
            [{"id": "unit", "text": "这是原文。"}],
            [
                {
                    "title": "张伟",
                    "type": "person",
                    "description": "张伟",
                    "source_id": "unit",
                    "source_span": [0, 2],
                }
            ],
            [],
            "example",
        )
