# Copyright (c) 2026 Microsoft Corporation.
# Licensed under the MIT License

"""Export shared pre-aggregation records without changing legacy graph results."""

from collections import defaultdict
from uuid import NAMESPACE_URL, uuid5

from bank_project.resolution.engine.contracts import Corpus, digest, require
from bank_project.resolution.engine.legacy import legacy_clean


def corpus_from_records(
    text_units: list[dict],
    entities: list[dict],
    relationships: list[dict],
    namespace: str,
) -> Corpus:
    """Keep chunk-level entity records and explicitly flag ambiguous edge endpoints.

    The legacy extractor may already conflate mentions within a chunk. This
    export measures resolution on its extracted records, not span detection.
    """
    sources = {str(row["id"]): row for row in text_units}
    require(len(sources) == len(text_units), "Duplicate text-unit IDs")
    occurrences, by_name = defaultdict(int), defaultdict(list)
    mentions = []
    for row in entities:
        source_id = str(row["source_id"])
        require(source_id in sources, "Extracted entity has unknown source_id")
        context = sources[source_id]["text"]
        signature = digest({"row": row, "context": context})
        occurrence = occurrences[signature]
        occurrences[signature] += 1
        mid = str(uuid5(NAMESPACE_URL, f"{namespace}:{signature}:{occurrence}"))
        mentions.append(
            {
                "mention_id": mid,
                "name": row["title"],
                "type": row["type"],
                "source_id": source_id,
                "context": context,
                "description": row["description"],
                **(
                    {"source_span": row["source_span"]}
                    if row.get("source_span") is not None
                    else {}
                ),
            }
        )
        by_name[source_id, legacy_clean(row["title"])].append(mid)
    relations, unresolved = [], []
    for ordinal, row in enumerate(relationships):
        source_id = str(row["source_id"])
        left = by_name[source_id, legacy_clean(row["source"])]
        right = by_name[source_id, legacy_clean(row["target"])]
        if len(left) != 1 or len(right) != 1:
            unresolved.append(
                {
                    "row": row,
                    "reason": "AMBIGUOUS_OR_MISSING_LOCAL_ENDPOINT",
                }
            )
            continue
        relations.append(
            {
                "relation_id": str(
                    uuid5(NAMESPACE_URL, f"{namespace}:relation:{digest(row)}:{ordinal}")
                ),
                "source_mention_id": left[0],
                "target_mention_id": right[0],
                "description": row["description"],
                "source_id": source_id,
            }
        )
    return Corpus.from_dict(
        {
            "schema_version": "er-corpus-v1",
            "namespace": namespace,
            "input_kind": "extracted_records",
            "mentions": mentions,
            "relations": relations,
            "metadata": {
                "source": "legacy_extractor_before_merge",
                "unresolved_relations": unresolved,
                "limitation": "Chunk entity records, not independently grounded surface mentions",
            },
        }
    )
