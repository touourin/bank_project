# Copyright (c) 2026 Microsoft Corporation.
# Licensed under the MIT License

"""Literal evidence windows and quote grounding for an individual mention."""

from __future__ import annotations

import re

from bank_project.resolution.engine.contracts import Mention

_SENTENCE_ENDINGS = frozenset("。！？!?；;\n")
_CLAUSE_ENDINGS = _SENTENCE_ENDINGS | frozenset("，,")
_ALIAS_CONNECTOR = re.compile(
    r"(?:亦即|也就是|即(?:为|是)?|又名|亦名|又称|亦称|也称|全称|简称|别名|缩写)"
    r"(?:为|是|叫作|叫)?[\s:：\"'“‘《「『]*$"
)


def _has_alias_bridge(context: str, left_end: int, right_start: int) -> bool:
    """Keep explicit alias evidence intact without joining separate clauses."""
    connector = context[left_end:right_start]
    return (
        len(connector) <= 48
        and not any(character in _CLAUSE_ENDINGS for character in connector)
        and _ALIAS_CONNECTOR.search(connector) is not None
    )


def target_window(mention: Mention) -> tuple[int, int]:
    """Return a short source window containing the explicitly located occurrence.

    Offsets use Python's Unicode character indexing and the end is exclusive.
    Legacy records without an occurrence span retain their complete context.
    The window bounds are a prompt aid; longer literal quotes remain valid.
    """
    context = mention.context
    if mention.source_span is None:
        return 0, len(context)
    start, end = mention.source_span
    left, right = max(0, start - 180), min(len(context), end + 180)
    for index in range(start - 1, left - 1, -1):
        if context[index] in _SENTENCE_ENDINGS:
            left = index + 1
            break
    for index in range(end, right):
        if context[index] in _SENTENCE_ENDINGS:
            right = index + 1
            break

    previous = context.rfind(mention.name, left, start)
    following = context.find(mention.name, end, right)
    # A repeated substring can be part of an explicit short/full-name bridge.
    # Preserve that clause, e.g. “北岚即北岚研究所”, instead of leaving “北岚即”.
    if (previous >= 0 and _has_alias_bridge(context, previous + len(mention.name), start)) or (
        following >= 0 and _has_alias_bridge(context, end, following)
    ):
        for index in range(start - 1, left - 1, -1):
            if context[index] in _CLAUSE_ENDINGS:
                left = index + 1
                break
        for index in range(end, right):
            if context[index] in _CLAUSE_ENDINGS:
                right = index + 1
                break
        return left, right

    # Separate identical names in one sentence so their attributes are not mixed.
    if previous >= 0:
        left = previous + len(mention.name)
        for index in range(start - 1, left - 1, -1):
            if context[index] in _CLAUSE_ENDINGS:
                left = index + 1
                break
    if following >= 0:
        right = following
        for index in range(end, right):
            if context[index] in _CLAUSE_ENDINGS:
                right = index + 1
                break
    return left, right


def quote_covers_target(mention: Mention, quote: str) -> bool:
    """Check literal quote occurrences against the intended source occurrence."""
    if mention.source_span is None:
        return quote in mention.context
    start, end = mention.source_span
    offset = mention.context.find(quote)
    while offset >= 0:
        if offset <= start and offset + len(quote) >= end:
            return True
        offset = mention.context.find(quote, offset + 1)
    return False
