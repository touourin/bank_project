# Copyright (c) 2026 Microsoft Corporation.
# Licensed under the MIT License

"""Frozen identity semantics of the pre-resolution standard indexing path."""

import html
import re
from uuid import NAMESPACE_URL, uuid5

from bank_project.resolution.engine.contracts import Corpus, Resolution


def legacy_clean(value: str) -> str:
    """Match GraphExtractor's upper() followed by clean_str(), exactly."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", html.unescape(value.upper().strip()))


def resolve_legacy(corpus: Corpus) -> Resolution:
    """Reproduce title/type grouping followed by first-title finalization.

    A discarded type group remains an explicitly dropped membership in the
    experiment, so evaluation cannot silently omit the lost source records.
    UUIDs are made deterministic for comparison; random UUID spelling in the
    old finalizer is not part of its entity identity decision.
    """
    first_type, groups, memberships = {}, {}, []
    for mention in corpus.mentions:
        title, entity_type = legacy_clean(mention.name), legacy_clean(mention.type)
        first_type.setdefault(title, entity_type)
        dropped = not title or first_type[title] != entity_type
        entity_id = (
            None
            if dropped
            else str(uuid5(NAMESPACE_URL, f"{corpus.namespace}:legacy_title_v1:{title}"))
        )
        memberships.append(
            {
                "mention_id": mention.mention_id,
                "entity_id": entity_id,
                "status": "dropped" if dropped else "resolved",
                "reason": "FINALIZE_TITLE_DEDUP" if dropped else "EXACT_TITLE_TYPE",
            }
        )
        if not dropped:
            group = groups.setdefault(
                entity_id,
                {
                    "entity_id": entity_id,
                    "name": title,
                    "type": entity_type,
                    "mention_ids": [],
                },
            )
            group["mention_ids"].append(mention.mention_id)
    return Resolution(
        "legacy_title_v1",
        corpus.sha256,
        memberships,
        list(groups.values()),
        diagnostics={
            "identity_stage": "standard_finalized_entities",
            "incremental": False,
            "dropped_records": sum(m["status"] == "dropped" for m in memberships),
        },
    )
