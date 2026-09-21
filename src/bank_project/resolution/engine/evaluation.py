# Copyright (c) 2026 Microsoft Corporation.
# Licensed under the MIT License

"""Gold-checked paired clustering metrics; never feed evaluation labels to judges."""

from collections import Counter, defaultdict

from bank_project.resolution.engine.contracts import Corpus, Resolution, require


def ratio(numerator: float, denominator: float) -> dict:
    """Preserve support counts and report empty denominators as null."""
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def annotation_template(corpus: Corpus) -> dict:
    """Create an explicitly unreviewed template, not invented gold labels."""
    return {
        "schema_version": "er-gold-v1",
        "corpus_sha256": corpus.sha256,
        "annotation_version": "replace-with-reviewed-version",
        "mentions": [
            {
                "mention_id": mention.mention_id,
                "gold_entity_id": None,
                "label": "unreviewed",
                "reviewed": False,
                "notes": "",
                "name": mention.name,
                "context": mention.context,
            }
            for mention in corpus.mentions
        ],
    }


def validate_gold(corpus: Corpus, gold: dict) -> dict[str, str | None]:
    """Require complete reviewed labels bound to the exact shared input."""
    require(
        isinstance(gold, dict) and gold.get("schema_version") == "er-gold-v1",
        "Expected er-gold-v1",
    )
    require(
        gold.get("corpus_sha256") == corpus.sha256,
        "Gold belongs to a different corpus snapshot",
    )
    require(
        isinstance(gold.get("annotation_version"), str)
        and bool(gold["annotation_version"].strip()),
        "Gold annotation version is missing",
    )
    require(isinstance(gold.get("mentions"), list), "Gold mentions must be a list")
    labels = {}
    for row in gold["mentions"]:
        require(isinstance(row, dict), "Invalid gold record")
        mid = row.get("mention_id")
        require(
            isinstance(mid, str) and mid not in labels,
            "Duplicate or invalid gold mention",
        )
        require(row.get("reviewed") is True, f"Gold mention {mid} is not reviewed")
        label, identity = row.get("label"), row.get("gold_entity_id")
        require(label in ("resolved", "insufficient"), f"Unfinished gold label for {mid}")
        require(
            (label == "insufficient" and identity is None)
            or (label == "resolved" and isinstance(identity, str) and bool(identity.strip())),
            f"Invalid gold identity for {mid}",
        )
        labels[mid] = identity
    require(
        set(labels) == {m.mention_id for m in corpus.mentions},
        "Gold must cover every input record exactly once",
    )
    return labels


def evaluate(corpus: Corpus, resolution: Resolution, labels: dict[str, str | None]) -> dict:
    """Score the complete record universe, including provisional and dropped rows."""
    require(
        resolution.corpus_sha256 == corpus.sha256,
        "Resolution belongs to another corpus",
    )
    memberships = {row["mention_id"]: row for row in resolution.memberships}
    require(
        len(memberships) == len(resolution.memberships) and set(memberships) == set(labels),
        "Resolution has missing, extra or duplicate memberships",
    )
    gold_groups, pred_groups, full_pred_groups = (
        defaultdict(set),
        defaultdict(set),
        defaultdict(set),
    )
    overlap = Counter()
    for mid, row in memberships.items():
        pid = row["entity_id"] or f"dropped:{mid}"
        full_pred_groups[pid].add(mid)
        if labels[mid] is not None:
            gold_groups[labels[mid]].add(mid)
            pred_groups[pid].add(mid)
            overlap[labels[mid], pid] += 1

    def pairs(n):
        return n * (n - 1) // 2

    tp = sum(pairs(n) for n in overlap.values())
    predicted_positive = sum(pairs(len(group)) for group in pred_groups.values())
    gold_positive = sum(pairs(len(group)) for group in gold_groups.values())
    fp, fn = predicted_positive - tp, gold_positive - tp
    precision_sum, recall_sum, labeled = 0.0, 0.0, sum(map(len, gold_groups.values()))
    for (gid, pid), count in overlap.items():
        precision_sum += count * count / len(pred_groups[pid])
        recall_sum += count * count / len(gold_groups[gid])
    bp = precision_sum / labeled if labeled else None
    br = recall_sum / labeled if labeled else None
    bf = 2 * bp * br / (bp + br) if bp is not None and br is not None and bp + br else None
    contaminated = [
        group for group in pred_groups.values() if len({labels[mid] for mid in group}) > 1
    ]
    candidate_eligible = [
        mid for mid, gid in labels.items() if gid is not None and len(gold_groups[gid]) > 1
    ]
    hit_count = sum(
        any(
            other != mid and labels.get(other) == labels[mid]
            for other in resolution.candidates.get(mid, [])
        )
        for mid in candidate_eligible
    )
    statuses = Counter(row["status"] for row in memberships.values())
    insufficient_forced = sum(
        labels[mid] is None and row["status"] == "resolved" for mid, row in memberships.items()
    )
    purity = {
        pid: (
            next(iter({labels[mid] for mid in members}))
            if all(labels[mid] is not None for mid in members)
            and len({labels[mid] for mid in members}) == 1
            else None
        )
        for pid, members in full_pred_groups.items()
    }
    relation_total, relation_correct = 0, 0
    for relation in corpus.relations:
        left, right = relation["source_mention_id"], relation["target_mention_id"]
        if labels[left] is None or labels[right] is None:
            continue
        relation_total += 1
        if all(
            memberships[mid]["entity_id"]
            and purity.get(memberships[mid]["entity_id"]) == labels[mid]
            for mid in (left, right)
        ):
            relation_correct += 1
    return {
        "evaluated_mentions": labeled,
        "insufficient_mentions": len(labels) - labeled,
        "pairwise": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": ratio(tp, tp + fp),
            "recall": ratio(tp, tp + fn),
            "f1": ratio(2 * tp, 2 * tp + fp + fn),
        },
        "b_cubed": {"precision": bp, "recall": br, "f1": bf},
        "contaminated_clusters": len(contaminated),
        "mentions_in_contaminated_clusters": ratio(sum(map(len, contaminated)), labeled),
        "statuses": dict(statuses),
        "retained_record_coverage": ratio(len(labels) - statuses["dropped"], len(labels)),
        "provisional_fraction": ratio(statuses["provisional"], len(labels)),
        "insufficient_forced_resolution": ratio(insufficient_forced, len(labels) - labeled),
        "candidate_recall": ratio(hit_count, len(candidate_eligible))
        if resolution.method != "legacy_title_v1"
        else None,
        "endpoint_cluster_identity_accuracy": ratio(relation_correct, relation_total),
        "notes": [
            "Clustering metrics use adjudicable gold records; insufficient records are reported separately.",
            "Provisional records remain singleton predictions. Dropped records are explicit singleton penalties plus retention loss.",
            "Candidate recall is batch mention-neighbor recall, not existing-registry Recall@K.",
            "Endpoint scoring requires pure labeled endpoint clusters; relation extraction semantics are not scored.",
            "Point estimates only; no production accuracy guarantee or release PASS.",
        ],
    }


def differences(corpus: Corpus, legacy: Resolution, evidence: Resolution) -> list[dict]:
    """Find partition differences without enumerating every possible record pair."""
    lookups, peers = [], []
    for result in (legacy, evidence):
        mapping = {row["mention_id"]: row for row in result.memberships}
        groups = defaultdict(set)
        for mid, row in mapping.items():
            groups[row["entity_id"] or f"dropped:{mid}"].add(mid)
        lookups.append(mapping)
        peers.append(groups)
    intersections = Counter()
    for mid in lookups[0]:
        intersections[
            lookups[0][mid]["entity_id"] or f"dropped:{mid}",
            lookups[1][mid]["entity_id"] or f"dropped:{mid}",
        ] += 1
    samples = [{key: sorted(group)[:40] for key, group in groups.items()} for groups in peers]
    changed = []
    for mention in corpus.mentions:
        mid = mention.mention_id
        old, new = lookups[0][mid], lookups[1][mid]
        old_key = old["entity_id"] or f"dropped:{mid}"
        new_key = new["entity_id"] or f"dropped:{mid}"
        old_peers, new_peers = peers[0][old_key], peers[1][new_key]
        shared = intersections[old_key, new_key]
        if len(old_peers) != shared or len(new_peers) != shared or old["status"] == "dropped":
            changed.append(
                {
                    "mention_id": mid,
                    "name": mention.name,
                    "legacy_entity_id": old["entity_id"],
                    "evidence_entity_id": new["entity_id"],
                    "legacy_cluster_size": len(old_peers),
                    "evidence_cluster_size": len(new_peers),
                    "removed_peer_count": len(old_peers) - shared,
                    "added_peer_count": len(new_peers) - shared,
                    "removed_peer_sample": [
                        item for item in samples[0][old_key] if item not in new_peers
                    ][:20],
                    "added_peer_sample": [
                        item for item in samples[1][new_key] if item not in old_peers
                    ][:20],
                }
            )
    return changed
