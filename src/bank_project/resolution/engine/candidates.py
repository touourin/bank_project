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


def retrieve_original(
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


TYPE_FAMILIES = {
    "organization": "organization",
    "organisation": "organization",
    "组织机构": "organization",
    "公司": "organization",
    "企业": "organization",
    "person": "person",
    "人物": "person",
    "人员": "person",
    "geo": "location",
    "location": "location",
    "地点": "location",
    "event": "event",
    "事件": "event",
    "company": "organization",
    "product": "product",
    "technology": "product",
    "产品或技术": "product",
    "financialmetric": "metric",
    "财务指标": "metric",
    "contract": "contract",
    "项目或合同": "contract",
    "战略关键词": "strategy",
    "account": "account",
    "账户": "account",
}


def compatible_types(left, right):
    """Unknown labels never exclude a pair; names/IDs may override this hint."""
    a, b = normalize(left.type), normalize(right.type)
    unknown = {"", "unknown", "other", "未知", "其他"}
    left_family, right_family = TYPE_FAMILIES.get(a), TYPE_FAMILIES.get(b)
    # DB schemas may use ontology IDs or domain-specific labels. Unmapped types
    # cannot establish an incompatibility and must not suppress name-based recall.
    return (
        a in unknown
        or b in unknown
        or left_family is None
        or right_family is None
        or left_family == right_family
    )


def identity_signals(left, right):
    """Ranking signals are recall hints, never proof of a shared identity."""
    a = {normalize(n) for n in (left.name, *left.aliases)} - {""}
    b = {normalize(n) for n in (right.name, *right.aliases)} - {""}
    exact = bool(a & b)
    identifier = bool(
        {(i.namespace, i.value) for i in left.identifiers}
        & {(i.namespace, i.value) for i in right.identifiers}
    )
    similarity = max((SequenceMatcher(None, x, y).ratio() for x in a for y in b), default=0)
    return identifier, exact, similarity


def retrieve(corpus, config, semantic_neighbors=None, *, diagnostics=None):
    """Select bounded recall; retain the original strategy for reproducible replays."""
    if config.retrieval_policy == "original":
        result = retrieve_original(corpus, config, semantic_neighbors)
        if diagnostics is not None:
            diagnostics.update(policy="original", omitted_by_policy=None)
        return result
    mentions = {m.mention_id: m for m in corpus.mentions}
    postings, names, descriptions, features = defaultdict(set), {}, {}, {}
    for mid, m in mentions.items():
        names[mid] = {normalize(n) for n in (m.name, *m.aliases)} - {""}
        descriptions[mid] = terms(m.description)
        keys = {"name:" + n for n in names[mid]}
        keys |= {"term:" + t for t in terms(" ".join((m.name, *m.aliases, m.description)))}
        keys |= {f"id:{i.namespace}:{i.value}" for i in m.identifiers}
        features[mid] = keys
        for key in keys:
            postings[key].add(mid)
    forced = defaultdict(set)
    for constraint in corpus.constraints:
        forced[constraint.left].add(constraint.right)
        forced[constraint.right].add(constraint.left)
    # Description-only recall needs multiple uncommon terms, not boilerplate.
    frequency_limit = max(2, int(len(mentions) * 0.1))
    rare = {
        mid: {t for t in ts if len(postings["term:" + t]) <= frequency_limit}
        for mid, ts in descriptions.items()
    }
    candidates, overflow, pairs, inspected, rejected = {}, set(), set(), set(), set()
    for mid in sorted(mentions):
        pool = set(forced[mid])
        for key in features[mid]:
            block = postings[key]
            if len(block) > config.max_block_size:
                overflow.add(mid)
                continue
            pool.update(block)
        vector_ids = set(semantic_neighbors[mid]) if semantic_neighbors is not None else set()
        pool.update(vector_ids)
        pool.discard(mid)
        ranked = []
        for other in sorted(pool):
            pair = tuple(sorted((mid, other)))
            inspected.add(pair)
            identifier, exact, similarity = identity_signals(mentions[mid], mentions[other])
            compatible = compatible_types(mentions[mid], mentions[other])
            shared_rare = len(rare[mid] & rare[other])
            strong = other in forced[mid] or identifier or exact
            eligible = strong or (
                compatible
                and (
                    similarity >= 0.55
                    or other in vector_ids
                    or (similarity >= 0.2 and shared_rare >= 2)
                )
            )
            if not eligible:
                rejected.add(pair)
                continue
            rank = (
                -int(other in forced[mid]),
                -int(identifier),
                -int(exact),
                -similarity,
                -int(other in vector_ids),
                -shared_rare,
                other,
            )
            ranked.append((rank, other))
        ranked.sort()
        if len(ranked) > config.candidate_limit:
            overflow.add(mid)
        candidates[mid] = sorted(
            {other for _, other in ranked[: config.candidate_limit]} | forced[mid]
        )
        pairs.update(tuple(sorted((mid, other))) for other in candidates[mid])
        require(
            len(pairs) <= config.max_pairs,
            "Candidate pair budget exceeded; split the experiment or increase max_pairs explicitly",
        )
    if diagnostics is not None:
        diagnostics.update(
            policy="balanced",
            inspected_pairs=len(inspected),
            omitted_by_policy=len(rejected - pairs),
            selected_pairs=len(pairs),
            rare_term_max_frequency=frequency_limit,
            recall_quality="not_measured_without_gold",
        )
    return candidates, overflow
