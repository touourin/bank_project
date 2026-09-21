# Copyright (c) 2026 Microsoft Corporation.
# Licensed under the MIT License

"""Occurrence grounding must preserve legacy inputs and reject borrowed quotes."""

from dataclasses import replace

import pytest

from bank_project.resolution.engine.contracts import Corpus, Mention
from bank_project.resolution.engine.evidence import quote_covers_target, target_window
from bank_project.resolution.engine.resolver import validate_judgment


def legacy_corpus():
    return Corpus(
        "legacy",
        (Mention("m1", "张伟", "PERSON", "doc1", "张伟在甲公司任职。"),),
    )


def located_mention():
    context = "张伟在甲公司负责财务，而张伟在乙公司负责研发。"
    start = context.rindex("张伟")
    return Mention("m2", "张伟", "PERSON", "doc2", context, source_span=(start, start + 2))


def judgment(quote, *, verdict="same"):
    return {
        "verdict": verdict,
        "left_quote": quote,
        "right_quote": "张伟在甲公司任职。",
        "reason": "Test literal quote grounding",
    }


def test_default_grounding_fields_preserve_legacy_serialization_and_hash():
    corpus = legacy_corpus()
    row = corpus.to_dict()["mentions"][0]
    assert "source_span" not in row
    assert "evidence_kind" not in row
    assert corpus.sha256 == "620a7c375d8456992b361aaee7621778467290f5bfe7cbcd72d7b872f1355e98"
    explicit_defaults = corpus.to_dict()
    explicit_defaults["mentions"][0].update(source_span=None, evidence_kind="source")
    assert Corpus.from_dict(explicit_defaults).sha256 == corpus.sha256


def test_grounding_fields_round_trip_and_affect_hash():
    corpus = legacy_corpus()
    located = replace(corpus, mentions=(replace(corpus.mentions[0], source_span=(0, 2)),))
    catalog = replace(corpus, mentions=(replace(corpus.mentions[0], evidence_kind="catalog"),))
    assert located.to_dict()["mentions"][0]["source_span"] == [0, 2]
    assert catalog.to_dict()["mentions"][0]["evidence_kind"] == "catalog"
    assert Corpus.from_dict(located.to_dict()) == located
    assert Corpus.from_dict(catalog.to_dict()) == catalog
    assert len({corpus.sha256, located.sha256, catalog.sha256}) == 3


@pytest.mark.parametrize(
    "span",
    [(-1, 1), (0, 200), (2, 2), (3, 2), (1, 3), (False, 2), (0, True), (0.0, 2)],
)
def test_invalid_source_spans_rejected_from_json_and_direct_construction(span):
    corpus = legacy_corpus()
    data = corpus.to_dict()
    data["mentions"][0]["source_span"] = list(span)
    with pytest.raises(ValueError, match="source_span"):
        Corpus.from_dict(data)
    with pytest.raises(ValueError, match="source_span"):
        replace(corpus.mentions[0], source_span=span)


@pytest.mark.parametrize("span", [[], [0], [0, 2, 3], "0,2", 2, {"start": 0, "end": 2}])
def test_malformed_source_spans_rejected(span):
    data = legacy_corpus().to_dict()
    data["mentions"][0]["source_span"] = span
    with pytest.raises(ValueError, match="source_span"):
        Corpus.from_dict(data)


@pytest.mark.parametrize("span", [(), (0,), (0, 2, 3), [0, 2], "0,2", 2])
def test_direct_mentions_reject_malformed_source_spans(span):
    with pytest.raises(ValueError, match="source_span"):
        replace(legacy_corpus().mentions[0], source_span=span)


@pytest.mark.parametrize("kind", ["annotated", "", None, True, 1])
def test_invalid_evidence_kind_rejected_from_json_and_direct_construction(kind):
    corpus = legacy_corpus()
    data = corpus.to_dict()
    data["mentions"][0]["evidence_kind"] = kind
    with pytest.raises(ValueError, match="evidence_kind"):
        Corpus.from_dict(data)
    with pytest.raises(ValueError, match="evidence_kind"):
        replace(corpus.mentions[0], evidence_kind=kind)


def test_source_span_uses_unicode_characters_not_bytes():
    mention = Mention("m", "张伟", "PERSON", "doc", "🙂张伟在甲公司任职。", source_span=(1, 3))
    assert mention.context[slice(*mention.source_span)] == "张伟"


@pytest.mark.parametrize("verdict", ["same", "different"])
def test_quote_from_another_same_named_occurrence_is_rejected(verdict):
    mention = located_mention()
    with pytest.raises(ValueError, match="target mention occurrence"):
        validate_judgment(
            judgment("张伟在甲公司负责财务", verdict=verdict), mention, legacy_corpus().mentions[0]
        )


def test_quote_covering_target_is_allowed():
    mention = located_mention()
    value = judgment("张伟在乙公司负责研发")
    assert validate_judgment(value, mention, legacy_corpus().mentions[0]) == value


def test_cross_sentence_quote_covering_target_is_allowed():
    mention = located_mention()
    prefix = "记录来自人事档案。"
    start, end = mention.source_span
    mention = replace(
        mention,
        context=prefix + mention.context,
        source_span=(start + len(prefix), end + len(prefix)),
    )
    value = judgment(mention.context)
    assert validate_judgment(value, mention, legacy_corpus().mentions[0]) == value


def test_repeated_identical_quote_checks_all_source_occurrences():
    context = "张伟负责研发。另一名张伟负责研发。"
    start = context.rindex("张伟")
    mention = Mention("m", "张伟", "PERSON", "doc", context, source_span=(start, start + 2))
    assert quote_covers_target(mention, "张伟负责研发")


def test_unlocated_legacy_records_keep_quote_behavior():
    mention = replace(located_mention(), source_span=None)
    value = judgment("张伟在甲公司负责财务")
    assert validate_judgment(value, mention, legacy_corpus().mentions[0]) == value
    assert target_window(mention) == (0, len(mention.context))


@pytest.mark.parametrize("quote", ["张伟", "并不存在的引文"])
def test_minimum_quote_length_and_literal_match_remain_required(quote):
    mention = located_mention()
    with pytest.raises(ValueError, match="source context"):
        validate_judgment(judgment(quote), mention, legacy_corpus().mentions[0])


def test_uncertain_verdict_does_not_require_quotes():
    value = judgment("", verdict="uncertain")
    assert validate_judgment(value, located_mention(), legacy_corpus().mentions[0]) == value


def test_target_window_is_literal_and_excludes_other_occurrences():
    mention = located_mention()
    start, end = target_window(mention)
    assert mention.context[start:end] == "而张伟在乙公司负责研发。"
    first = replace(mention, source_span=(0, 2))
    start, end = target_window(first)
    assert first.context[start:end] == "张伟在甲公司负责财务，"


def test_target_window_respects_sentence_boundaries_and_size():
    context = "上一句。" + "甲" * 250 + "张伟" + "乙" * 250 + "。下一句。"
    start = context.index("张伟")
    mention = Mention("m", "张伟", "PERSON", "doc", context, source_span=(start, start + 2))
    left, right = target_window(mention)
    assert left <= start < start + 2 <= right
    assert right - left <= len(mention.name) + 360


@pytest.mark.parametrize("connector", ["即", "亦即", "又名", "亦称", "全称为", "简称是"])
def test_target_window_preserves_explicit_alias_bridge(connector):
    from bank_project.resolution.engine.model_judge import judgment_record

    clause = f"北岚{connector}北岚研究所，"
    context = clause + "北岚设在海港。"
    mention = Mention("m", "北岚", "ORGANIZATION", "doc", context, source_span=(0, 2))
    left, right = target_window(mention)
    assert context[left:right] == clause
    options = judgment_record(mention, "L")["evidence_options"]
    assert options and options[0]["text"] == clause
    assert len(options[0]["text"].strip()) >= 4


def test_alias_bridge_is_retained_for_the_repeated_substring_target():
    context = "北岚即北岚研究所，北岚设在海港。"
    start = context.index("北岚", 2)
    mention = Mention("m", "北岚", "ORGANIZATION", "doc", context, source_span=(start, start + 2))
    left, right = target_window(mention)
    assert context[left:right] == "北岚即北岚研究所，"


def test_full_name_to_short_name_bridge_is_retained():
    context = "北岚研究所简称北岚，北岚设在海港。"
    start = context.index("北岚", 2)
    mention = Mention("m", "北岚", "ORGANIZATION", "doc", context, source_span=(start, start + 2))
    left, right = target_window(mention)
    assert context[left:right] == "北岚研究所简称北岚，"


def test_alias_bridge_does_not_capture_a_same_name_in_the_next_clause():
    context = "北岚即北岚研究所，北岚是一处村庄。"
    start = context.rindex("北岚")
    mention = Mention("m", "北岚", "LOCATION", "doc", context, source_span=(start, start + 2))
    left, right = target_window(mention)
    assert context[left:right] == "北岚是一处村庄。"


def test_plain_copula_does_not_join_same_named_entities():
    context = "北岚是北岚公司的一名职员，北岚负责招聘。"
    mention = Mention("m", "北岚", "PERSON", "doc", context, source_span=(0, 2))
    left, right = target_window(mention)
    assert context[left:right] == "北岚是"
