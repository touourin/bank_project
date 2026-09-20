# Copyright (c) 2026 Microsoft Corporation.
# Licensed under the MIT License

"""Bounded lexical, alias and identifier retrieval for a shared mention corpus."""

import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

from bank_project.resolution.engine.contracts import Corpus, ResolverConfig, require


def normalize(value: str) -> str:
    """Normalize retrieval text without changing source evidence."""
    return "".join(
        char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum()
    )


def terms(value: str) -> set[str]:
    """Use words and Chinese bigrams without requiring a tokenizer model."""
    words = set(re.findall(r"[a-z0-9]{2,}", value.casefold()))
    for run in re.findall(r"[\u3400-\u9fff]+", value):
        words.update(run[i : i + 2] for i in range(len(run) - 1))
    return words


def retrieve(
    corpus: Corpus,
    config: ResolverConfig,
    semantic_neighbors: dict[str, list[str]] | None = None,
) -> tuple[dict[str, list[str]], set[str]]:
    """Return candidates and high-ambiguity records requiring conservative handling."""
    mentions = {m.mention_id: m for m in corpus.mentions}
    names, features, postings = {}, {}, defaultdict(set)
    for mid, mention in mentions.items():
        names[mid] = {normalize(name) for name in (mention.name, *mention.aliases)} - {""}
        keys = {f"name:{name}" for name in names[mid]}
        keys.update(
            f"term:{term}"
            for term in terms(" ".join((mention.name, *mention.aliases, mention.description)))
        )
        keys.update(f"id:{item.namespace}:{item.value}" for item in mention.identifiers)
        features[mid] = keys
        for key in keys:
            postings[key].add(mid)
    forced = defaultdict(set)
    for constraint in corpus.constraints:
        forced[constraint.left].add(constraint.right)
        forced[constraint.right].add(constraint.left)
    candidates, overflow, pairs = {}, set(), set()
    for mid in sorted(mentions):
        pool = set(forced[mid])
        for key in sorted(features[mid]):
            block = postings[key]
            if len(block) > config.max_block_size:
                # Every skipped recall block affects completeness, including common
                # description terms. Preserve all records and disclose the omission.
                overflow.add(mid)
                continue
            pool.update(block)
        pool.discard(mid)

        def score(other, mid=mid):
            shared = features[mid] & features[other]
            exact = bool(names[mid] & names[other])
            identifier = any(key.startswith("id:") for key in shared)
            similarity = max(
                (
                    SequenceMatcher(None, left, right).ratio()
                    for left in names[mid]
                    for right in names[other]
                ),
                default=0,
            )
            return (other in forced[mid], identifier, exact, similarity, len(shared))

        ranked = sorted(
            pool,
            key=lambda other: (tuple(-float(value) for value in score(other)), other),
        )
        if semantic_neighbors is not None:
            lexical_ranks = {other: rank + 1 for rank, other in enumerate(ranked)}
            vector_ranks = {other: rank + 1 for rank, other in enumerate(semantic_neighbors[mid])}
            pool.update(vector_ranks)

            def fused_score(other, mid=mid, lexical_ranks=lexical_ranks, vector_ranks=vector_ranks):
                rrf = sum(
                    1 / (60 + ranks[other])
                    for ranks in (lexical_ranks, vector_ranks)
                    if other in ranks
                )
                return (
                    -int(other in forced[mid]),
                    -int(any(key.startswith("id:") for key in features[mid] & features[other])),
                    -int(bool(names[mid] & names[other])),
                    -rrf,
                    other,
                )

            ranked = sorted(pool, key=fused_score)
        if len(ranked) > config.candidate_limit:
            overflow.add(mid)
        candidates[mid] = sorted(set(ranked[: config.candidate_limit]) | forced[mid])
        pairs.update(tuple(sorted((mid, other))) for other in candidates[mid])
        require(
            len(pairs) <= config.max_pairs,
            "Candidate pair budget exceeded; split the experiment or increase max_pairs explicitly",
        )
    return candidates, overflow
