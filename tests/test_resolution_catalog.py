# Copyright (c) 2026 Microsoft Corporation.
# Licensed under the MIT License

"""Competitive catalogue linking must be anonymous, grounded and conservative."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from bank_project.resolution.engine.catalog import (
    build_catalog_payload,
    normalize_catalog_result,
)
from bank_project.resolution.engine.contracts import Mention
from bank_project.resolution.engine.model_judge import judgment_record


def source():
    context = "华为是一家企业。华为品牌在消费者中有较高知名度。"
    start = context.rindex("华为")
    return Mention(
        "source-private-id",
        "华为",
        "ORGANIZATION",
        "private-document-id",
        context,
        source_span=(start, start + 2),
    )


def candidates():
    return [
        Mention(
            "goldlike-company-id",
            "华为公司",
            "ORGANIZATION",
            "catalog-private-id",
            "华为公司是一家科技企业。",
            aliases=("华为", "华为技术有限公司"),
            evidence_kind="catalog",
        ),
        Mention(
            "goldlike-brand-id",
            "华为品牌",
            "BRAND",
            "catalog-private-id",
            "华为品牌用于其面向消费者的商品。",
            aliases=("华为",),
            evidence_kind="catalog",
        ),
    ]


def setup_selection():
    mention, entries = source(), candidates()
    payload = build_catalog_payload(mention, entries)
    selected = next(record for record in payload["candidates"] if record["type"] == "BRAND")
    raw = {
        "selected_candidate": selected["ref"],
        "source_evidence_id": payload["source"]["evidence_options"][0]["id"],
        "candidate_evidence_id": selected["evidence_options"][0]["id"],
        "reason": "目标处明确描述面向消费者的品牌含义。",
    }
    return mention, entries, payload, raw


def test_payload_has_anonymous_content_stable_candidate_order():
    mention, entries = source(), candidates()
    payload = build_catalog_payload(mention, entries)
    renamed = [
        replace(item, mention_id=f"changed-{i}", source_id=f"changed-doc-{i}")
        for i, item in enumerate(reversed(entries))
    ]
    assert payload == build_catalog_payload(
        replace(mention, mention_id="changed-source", source_id="changed-source-doc"), renamed
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "private" not in serialized
    assert "goldlike" not in serialized
    assert "mention_id" not in serialized
    assert "source_id" not in serialized
    assert [record["ref"] for record in payload["candidates"]] == ["C0", "C1"]
    assert payload["source"]["target"]["text"] == mention.name
    assert all(record["evidence_options"] for record in payload["candidates"])


def test_payload_drops_untrusted_adapter_metadata():
    def custom_record(mention, side):
        return {**judgment_record(mention, side), "gold": "private-gold", "runtime_id": "private"}

    payload = build_catalog_payload(source(), candidates(), record_builder=custom_record)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "private" not in serialized
    assert "gold" not in serialized


def test_alias_order_does_not_affect_candidate_order_or_payload():
    entries = candidates()
    changed = [replace(item, aliases=tuple(reversed(item.aliases))) for item in entries]
    assert build_catalog_payload(source(), entries) == build_catalog_payload(source(), changed)


def test_selection_only_asserts_same_for_the_selected_candidate():
    mention, entries, payload, raw = setup_selection()
    results = normalize_catalog_result(raw, payload, mention, entries)
    assert results["goldlike-brand-id"]["verdict"] == "same"
    assert results["goldlike-company-id"]["verdict"] == "uncertain"
    assert results["goldlike-brand-id"]["left_quote"] == "华为品牌在消费者中有较高知名度。"
    assert results["goldlike-brand-id"]["right_quote"] == entries[1].context
    assert normalize_catalog_result(raw, payload, mention, list(reversed(entries))) == results


def test_nil_requires_valid_source_evidence_and_marks_all_candidates_different():
    mention, entries, payload, raw = setup_selection()
    raw.update(selected_candidate="NIL", candidate_evidence_id=None)
    results = normalize_catalog_result(raw, payload, mention, entries)
    assert all(value["verdict"] == "different" for value in results.values())
    assert {value["right_quote"] for value in results.values()} == {
        entry.context for entry in entries
    }
    raw["source_evidence_id"] = None
    with pytest.raises(ValueError, match="quote"):
        normalize_catalog_result(raw, payload, mention, entries)


def test_uncertain_needs_no_quotes_and_never_claims_candidate_conflicts():
    mention, entries, payload, raw = setup_selection()
    raw.update(selected_candidate="uncertain", source_evidence_id=None, candidate_evidence_id=None)
    results = normalize_catalog_result(raw, payload, mention, entries)
    assert all(value["verdict"] == "uncertain" for value in results.values())
    assert all(value["left_quote"] == value["right_quote"] == "" for value in results.values())


def test_identical_catalogue_entries_cannot_be_selected_by_opaque_ids():
    mention = source()
    candidate = candidates()[1]
    entries = [candidate, replace(candidate, mention_id="another-private-id", source_id="other")]
    payload = build_catalog_payload(mention, entries)
    raw = {
        "selected_candidate": "C0",
        "source_evidence_id": "S0",
        "candidate_evidence_id": "C0E0",
        "reason": "品牌义项相符。",
    }
    results = normalize_catalog_result(raw, payload, mention, entries)
    assert all(value["verdict"] == "uncertain" for value in results.values())
    assert build_catalog_payload(mention, list(reversed(entries))) == payload


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("selected_candidate", "goldlike-brand-id"),
        ("selected_candidate", "C99"),
        ("selected_candidate", []),
        ("source_evidence_id", "S99"),
        ("source_evidence_id", None),
        ("candidate_evidence_id", "C99E0"),
        ("candidate_evidence_id", None),
        ("reason", ""),
    ],
)
def test_unknown_or_missing_result_fields_fail_strictly(field, value):
    mention, entries, payload, raw = setup_selection()
    raw[field] = value
    with pytest.raises(ValueError):
        normalize_catalog_result(raw, payload, mention, entries)


def test_evidence_id_from_another_candidate_is_rejected():
    mention, entries, payload, raw = setup_selection()
    other = next(
        record for record in payload["candidates"] if record["ref"] != raw["selected_candidate"]
    )
    raw["candidate_evidence_id"] = other["evidence_options"][0]["id"]
    with pytest.raises(ValueError, match="unknown evidence"):
        normalize_catalog_result(raw, payload, mention, entries)


@pytest.mark.parametrize("invalid_quote", ["华为是一家企业。", "模型编造的品牌证据。", "华为"])
def test_source_quote_must_be_literal_and_cover_the_target(invalid_quote):
    mention, entries, payload, raw = setup_selection()
    payload["source"]["evidence_options"][0]["text"] = invalid_quote
    with pytest.raises(ValueError, match="quote"):
        normalize_catalog_result(raw, payload, mention, entries)


def test_candidate_quote_cannot_be_fabricated():
    mention, entries, payload, raw = setup_selection()
    record = next(
        record for record in payload["candidates"] if record["ref"] == raw["selected_candidate"]
    )
    record["evidence_options"][0]["text"] = "不在原目录中的品牌证据。"
    with pytest.raises(ValueError, match="quote"):
        normalize_catalog_result(raw, payload, mention, entries)


def test_uncertain_does_not_hide_unknown_evidence_ids():
    mention, entries, payload, raw = setup_selection()
    raw.update(selected_candidate="uncertain", source_evidence_id="S99", candidate_evidence_id=None)
    with pytest.raises(ValueError, match="quote"):
        normalize_catalog_result(raw, payload, mention, entries)


def test_duplicate_evidence_ids_are_rejected():
    mention, entries, payload, raw = setup_selection()
    payload["source"]["evidence_options"].append(deepcopy(payload["source"]["evidence_options"][0]))
    with pytest.raises(ValueError, match="quote"):
        normalize_catalog_result(raw, payload, mention, entries)


def test_source_records_cannot_be_used_as_catalogue_candidates():
    with pytest.raises(ValueError, match="catalogue evidence"):
        build_catalog_payload(source(), [replace(candidates()[0], evidence_kind="source")])


def test_duplicate_candidate_ids_fail_instead_of_overwriting_results():
    entries = candidates()
    entries[1] = replace(entries[1], mention_id=entries[0].mention_id)
    with pytest.raises(ValueError, match="Duplicate"):
        build_catalog_payload(source(), entries)
