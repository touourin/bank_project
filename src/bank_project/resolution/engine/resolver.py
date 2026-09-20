# Copyright (c) 2026 Microsoft Corporation.
# Licensed under the MIT License

"""Evidence-based matching with explicit abstention and complete-link guards."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from bank_project.resolution.engine.candidates import retrieve
from bank_project.resolution.engine.contracts import (
    Corpus,
    Mention,
    Resolution,
    ResolverConfig,
    require,
)


class IdentityJudge(Protocol):
    """A pluggable, versioned pair classifier, independent of evaluation labels."""

    version: str

    async def judge(self, left: Mention, right: Mention) -> dict:
        """Return verdict, left_quote, right_quote and an explanatory reason."""
        ...


def verified_ids(mention: Mention, config: ResolverConfig) -> dict[str, str]:
    """Only policy-approved, upstream-reviewed identifier assertions are hard facts."""
    result = {}
    for item in mention.identifiers:
        if item.verified and item.namespace in config.unique_id_namespaces:
            require(
                item.namespace not in result or result[item.namespace] == item.value,
                f"Conflicting verified identifiers inside {mention.mention_id}",
            )
            result[item.namespace] = item.value
    return result


def validate_judgment(value: dict, left: Mention, right: Mention) -> dict:
    """Validate model quotes; semantic support still requires quality evaluation."""
    require(isinstance(value, dict), "Judge response must be an object")
    require(
        value.get("verdict") in ("same", "different", "uncertain"),
        "Invalid judge verdict",
    )
    require(
        isinstance(value.get("reason"), str) and bool(value["reason"].strip()),
        "Judge reason is missing",
    )
    if value["verdict"] != "uncertain":
        for side, mention in (("left", left), ("right", right)):
            quote = value.get(f"{side}_quote")
            require(
                isinstance(quote, str) and len(quote.strip()) >= 4 and quote in mention.context,
                f"Judge {side} quote is not supported by source context",
            )
    return {key: value.get(key, "") for key in ("verdict", "left_quote", "right_quote", "reason")}


async def resolve_evidence(
    corpus: Corpus,
    config: ResolverConfig | None = None,
    judge: IdentityJudge | None = None,
    vectors: dict | None = None,
    *,
    retrieval_corpus: Corpus | None = None,
    method: str = "evidence_v1",
    model_only: bool = False,
    candidate_data: tuple[dict[str, list[str]], set[str]] | None = None,
) -> Resolution:
    """Resolve the same input records as the legacy baseline without mutating it."""
    config = config or ResolverConfig()
    mentions = {m.mention_id: m for m in corpus.mentions}
    ids = {mid: verified_ids(mention, config) for mid, mention in mentions.items()}
    constraints = {tuple(sorted((c.left, c.right))): c for c in corpus.constraints}
    semantic_neighbors = None
    if vectors is not None:
        from bank_project.resolution.engine.vectors import vector_neighbors

        semantic_neighbors = vector_neighbors(corpus, vectors, config)
    candidates, overflow = (
        candidate_data
        if candidate_data is not None
        else retrieve(retrieval_corpus or corpus, config, semantic_neighbors)
    )
    decisions, model_calls = {}, 0
    semaphore = asyncio.Semaphore(config.concurrency)

    def rule(pair):
        left, right = pair
        common = ids[left].keys() & ids[right].keys()
        conflict = any(ids[left][key] != ids[right][key] for key in common)
        constraint = constraints.get(pair)
        if constraint:
            require(
                not (constraint.verdict == "same" and conflict),
                "Reviewed same constraint conflicts with trusted identity",
            )
            if model_only and constraint.verdict == "same":
                return None
            return {
                "verdict": constraint.verdict,
                "reason": constraint.reason,
                "origin": "reviewed_constraint",
            }
        if conflict:
            return {
                "verdict": "different",
                "reason": "TRUSTED_IDENTIFIER_CONFLICT",
                "origin": "rule",
            }
        if common and not model_only:
            return {
                "verdict": "same",
                "reason": "TRUSTED_IDENTIFIER_MATCH",
                "origin": "rule",
            }
        return None

    async def compare(pair):
        nonlocal model_calls
        if pair in decisions:
            return decisions[pair]
        result = rule(pair)
        if result is None:
            if judge is None:
                result = {
                    "verdict": "uncertain",
                    "reason": "NO_IDENTITY_EVIDENCE",
                    "origin": "unresolved",
                }
            elif model_calls >= config.max_model_calls:
                result = {
                    "verdict": "uncertain",
                    "reason": "MODEL_BUDGET_EXHAUSTED",
                    "origin": "error",
                }
            else:
                model_calls += 1
                try:
                    async with semaphore:
                        raw = await asyncio.wait_for(
                            judge.judge(mentions[pair[0]], mentions[pair[1]]),
                            timeout=config.timeout_seconds,
                        )
                    result = {
                        **validate_judgment(raw, mentions[pair[0]], mentions[pair[1]]),
                        "origin": "model",
                    }
                except (TimeoutError, ValueError, RuntimeError, OSError) as exc:
                    result = {
                        "verdict": "uncertain",
                        "reason": "JUDGE_FAILED",
                        "error_type": type(exc).__name__,
                        "origin": "error",
                    }
        result = {"left": pair[0], "right": pair[1], "accepted": False, **result}
        if result["verdict"] == "same" and result["origin"] == "model":
            result["model_reason"] = result["reason"]
            if config.model_policy != "apply":
                result["proposal"] = "same"
                result["verdict"] = "uncertain"
                result["reason"] = "MODEL_MATCH_REQUIRES_REVIEW"
            elif set(pair) & overflow:
                result["proposal"] = "same"
                result["verdict"] = "uncertain"
                result["reason"] = "CANDIDATE_OVERFLOW"
        decisions[pair] = result
        return result

    initial_pairs = sorted(
        {tuple(sorted((mid, other))) for mid, others in candidates.items() for other in others}
    )
    # Batches bound the number of allocated coroutines as well as active calls.
    for offset in range(0, len(initial_pairs), config.concurrency):
        await asyncio.gather(
            *(compare(pair) for pair in initial_pairs[offset : offset + config.concurrency])
        )
    groups = {mid: {mid} for mid in mentions}
    owner = {mid: mid for mid in mentions}
    blocked = []
    proposals = sorted(
        (pair for pair in initial_pairs if decisions[pair]["verdict"] == "same"),
        key=lambda pair: (decisions[pair]["origin"] == "model", pair),
    )
    for pair in proposals:
        left_owner, right_owner = owner[pair[0]], owner[pair[1]]
        if left_owner == right_owner:
            decisions[pair]["accepted"] = True
            continue
        left_group, right_group = groups[left_owner], groups[right_owner]
        if len(left_group) + len(right_group) > config.max_cluster_size:
            blocked.append(
                {
                    "left": pair[0],
                    "right": pair[1],
                    "reason": "CLUSTER_SIZE_REVIEW",
                }
            )
            continue
        compatible = True
        for left in sorted(left_group):
            for right in sorted(right_group):
                cross_pair = tuple(sorted((left, right)))
                if cross_pair not in decisions and len(decisions) >= config.max_pairs:
                    compatible = False
                    break
                verdict = await compare(cross_pair)
                if verdict["verdict"] != "same":
                    compatible = False
                    break
            if not compatible:
                break
        if not compatible:
            blocked.append(
                {
                    "left": pair[0],
                    "right": pair[1],
                    "reason": "CLUSTER_IDENTITY_NOT_CONFIRMED",
                }
            )
            continue
        survivor, retired = min(left_owner, right_owner), max(left_owner, right_owner)
        merged = left_group | right_group
        groups[survivor] = merged
        del groups[retired]
        for mid in merged:
            owner[mid] = survivor
        decisions[pair]["accepted"] = True

    entities, memberships = [], []
    for anchor, members in sorted(groups.items()):
        entity_id = str(uuid5(NAMESPACE_URL, f"{corpus.namespace}:{method}:{anchor}"))
        status = "resolved" if len(members) > 1 else "provisional"
        entities.append(
            {
                "entity_id": entity_id,
                "name": mentions[anchor].name,
                "type": mentions[anchor].type,
                "mention_ids": sorted(members),
                "status": status,
            }
        )
        memberships.extend(
            {
                "mention_id": mid,
                "entity_id": entity_id,
                "status": status,
                "reason": "VERIFIED_CLUSTER" if status == "resolved" else "KEPT_SEPARATE",
            }
            for mid in sorted(members)
        )
    membership_ids = {item["mention_id"]: item["entity_id"] for item in memberships}
    for pair, constraint in constraints.items():
        if constraint.verdict == "different":
            require(
                membership_ids[pair[0]] != membership_ids[pair[1]],
                "Resolver violated cannot-link",
            )
    return Resolution(
        method,
        corpus.sha256,
        memberships,
        entities,
        [decisions[pair] for pair in sorted(decisions)],
        candidates,
        {
            "config": asdict(config),
            "merge_decision_policy": "model_required"
            if model_only
            else "evidence_rules_then_model",
            "judge_version": judge.version if judge else None,
            "judge_requests": model_calls,
            "judge_failures": sum(item["reason"] == "JUDGE_FAILED" for item in decisions.values()),
            "completion_calls": getattr(judge, "calls", None),
            "judgment_cache_hits": getattr(judge, "cache_hits", None),
            "reviewed_constraints": len(corpus.constraints),
            "trusted_identifier_assertions": sum(len(values) for values in ids.values()),
            "candidate_overflow": sorted(overflow),
            "blocked_merges": blocked,
            "experimental_model_application": config.model_policy == "apply",
            "id_stability": "same corpus and policy; not an incremental identity registry",
        },
    )
