"""Rebuild reversible derivatives from immutable snapshots and accepted decisions."""

from copy import deepcopy

from bank_project.alignment.models import AlignmentError

from .adapter import brief, conflicts
from .engine.contracts import digest
from .models import Merge, Summary


def memberships(graph, candidates):
    owner = {node["id"]: node["id"] for node in graph["nodes"]}
    groups = {key: {key} for key in owner}
    for candidate in sorted(candidates, key=lambda row: (row.decision_order, row.id)):
        if candidate.status != "merged":
            continue
        anchors = {owner[mid] for mid in candidate.node_ids}
        members = set().union(*(groups[key] for key in anchors))
        if len(members) > 100:
            raise AlignmentError("合并后超过 100 个来源节点，请拆分核验，避免误合大簇", 409)
        canonical = candidate.canonical_id or candidate.node_ids[0]
        if canonical not in members:
            raise AlignmentError("保留节点不属于待合并实体簇", 409)
        for anchor in anchors:
            del groups[anchor]
        groups[canonical] = members
        for mid in members:
            owner[mid] = canonical
    # Explicit rejected identity pairs cannot be bypassed by a transitive bridge.
    for candidate in candidates:
        if candidate.status == "rejected" and len({owner[mid] for mid in candidate.node_ids}) < len(
            candidate.node_ids
        ):
            raise AlignmentError("本次决定与已拒绝的实体关联冲突；请先撤销相应拒绝或合并", 409)
    return owner, groups


def update_run(run, graph):
    owner, groups = memberships(graph, run.candidates)
    by_id = {node["id"]: node for node in graph["nodes"]}
    run.summary = Summary(
        original_node_count=len(graph["nodes"]),
        original_edge_count=len(graph["edges"]),
        node_count=len(groups),
        edge_count=len(graph["edges"]),
        pending_count=sum(c.status == "pending" for c in run.candidates),
        merged_count=sum(c.status == "merged" for c in run.candidates),
        rejected_count=sum(c.status == "rejected" for c in run.candidates),
    )
    run.merges = [
        Merge(
            candidate_id=candidate.id,
            source_nodes=[
                brief(by_id[mid]) for mid in sorted(groups[owner[candidate.node_ids[0]]])
            ],
            target_node=brief(by_id[owner[candidate.node_ids[0]]]),
        )
        for candidate in run.candidates
        if candidate.status == "merged"
    ]
    return run


def project(run, graph):
    owner, groups = memberships(graph, run.candidates)
    by_id = {node["id"]: node for node in graph["nodes"]}
    result = deepcopy(graph)
    result.update(
        {
            "id": run.id,
            "name": f"{run.name} · 消歧版本 {run.revision}",
            "source_kind": run.source_kind,
            "source_id": run.source_id,
            "parent_graph": {
                "id": graph["id"],
                "source_kind": run.source_kind,
                "source_id": run.source_id,
            },
            "resolution": {
                "run_id": run.id,
                "revision": run.revision,
                "source_sha256": run.diagnostics["source_sha256"],
                "memberships": owner,
            },
        }
    )
    result["nodes"] = []
    for canonical, member_ids in groups.items():
        node = deepcopy(by_id[canonical])
        if len(member_ids) > 1:
            members = [by_id[mid] for mid in sorted(member_ids)]
            # Keep canonical properties byte-for-byte equivalent. All alternate values and
            # complete original records live alongside them, so undo never loses evidence.
            node["resolution"] = {
                "canonical_id": canonical,
                "member_ids": sorted(member_ids),
                "source_nodes": deepcopy(members),
                "conflicts": [item.model_dump() for item in conflicts(members)],
                "property_values": _property_values(members),
            }
        result["nodes"].append(node)
    result["edges"] = []
    for original in graph["edges"]:
        edge = deepcopy(original)
        edge["source"], edge["target"] = owner[original["source"]], owner[original["target"]]
        if edge["source"] != original["source"] or edge["target"] != original["target"]:
            edge["resolution"] = {
                "original_source": original["source"],
                "original_target": original["target"],
                "source_edge": deepcopy(original),
            }
        # Do not deduplicate parallel edges or discard self edges: both carry source facts.
        result["edges"].append(edge)
    result["summary"] = {
        **result.get("summary", {}),
        "node_count": len(result["nodes"]),
        "edge_count": len(result["edges"]),
    }
    result["sha256"] = digest({"nodes": result["nodes"], "edges": result["edges"]})
    return result


def _property_values(nodes):
    values = {}
    for node in nodes:
        for key, value in node["properties"].items():
            values.setdefault(key, []).append({"node_id": node["id"], "value": deepcopy(value)})
    return values
