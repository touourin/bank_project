"""Pure, deterministic WHY applicability traversal over a pinned concept graph.

Paths run from the anchor to WHY sources; edge ``direction`` records the reverse
rule flow, from the source back to the anchor. IS_A may have multiple parents.
"""

import hashlib
import heapq
import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from bank_project.alignment.models import AlignmentError

PROPAGATION_POLICY_VERSION = "bank.risk.propagation-policy.v2"
_STRENGTH_RANK = {"deductive": 0, "strong": 1, "weak": 2}
_RANK_STRENGTH = {rank: name for name, rank in _STRENGTH_RANK.items()}
_PRIORITY_ORDER = ("高", "中", "低")

# Stored source -> target direction. Unregistered relations never transmit rules.
RELATION_RULE_FLOW: dict[str, dict[str, str | None]] = {
    "inheres_in": {"forward": "strong", "reverse": None},
    "has_participant": {"forward": "strong", "reverse": "weak"},
    "preceded_by": {"forward": None, "reverse": "weak"},
    "continuant_part_of": {"forward": "strong", "reverse": None},
    "occurrent_part_of": {"forward": "strong", "reverse": None},
    "located_in": {"forward": None, "reverse": "strong"},
    "derives_from": {"forward": None, "reverse": "strong"},
    "adjacent_to": {"forward": None, "reverse": None},
}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


@dataclass(frozen=True)
class ConceptGraph:
    nodes: dict[str, dict[str, Any]]
    parents: dict[str, tuple[str, ...]]
    children: dict[str, tuple[str, ...]]
    rel_out: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    rel_in: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)


def _anchor(graph: ConceptGraph, anchor_node_id: str) -> None:
    if anchor_node_id not in graph.nodes:
        raise AlignmentError("传导锚点不在所选本体版本中")


def _limit(value: int, minimum: int, name: str) -> None:
    if type(value) is not int or value < minimum:
        raise AlignmentError(f"{name}必须为不小于 {minimum} 的整数")


def _neighbors(graph: ConceptGraph, node: str):
    for parent in sorted(graph.parents.get(node, ())):
        yield parent, "is_a_down", "is_a", "reverse", "deductive"
    for relation, source in sorted(graph.rel_in.get(node, ())):
        strength = RELATION_RULE_FLOW.get(relation, {}).get("forward")
        if strength:
            yield source, "relation", relation, "forward", strength
    for relation, target in sorted(graph.rel_out.get(node, ())):
        strength = RELATION_RULE_FLOW.get(relation, {}).get("reverse")
        if strength:
            yield target, "relation", relation, "reverse", strength


def propagate(
    graph: ConceptGraph, anchor_node_id: str, *, max_depth: int = 5, max_candidates: int = 50
) -> dict[str, Any]:
    """Find the strongest bounded path to each WHY source, with explicit limits.

    State includes strength and relation depth: a weaker but shorter prefix can
    reach a source that a stronger prefix exhausts the budget before reaching.
    Keeping strength separately also avoids dropping a shorter IS_A path before
    a later weak edge makes the two path strengths equal. Each state retains its
    fewest IS_A hops, breaking ties by the entire path for replayable output.
    """
    _anchor(graph, anchor_node_id)
    _limit(max_depth, 0, "关系跳数")
    _limit(max_candidates, 1, "候选上限")
    # Route tuples carry (from, to, kind, relation, rule-flow direction, strength).
    start = (anchor_node_id, 0, 0)
    best = {start: (0, ())}
    heap = [(0, 0, 0, (), anchor_node_id)]
    depth_blocked: set[str] = set()
    while heap:
        worst, relation_hops, is_a_hops, route, node = heapq.heappop(heap)
        if best.get((node, worst, relation_hops)) != (is_a_hops, route):
            continue
        for neighbor, kind, relation, direction, strength in _neighbors(graph, node):
            if kind == "relation" and relation_hops == max_depth:
                depth_blocked.add(neighbor)
                continue
            next_worst = max(worst, _STRENGTH_RANK[strength])
            next_depth = relation_hops + (kind == "relation")
            next_is_a = is_a_hops + (kind == "is_a_down")
            next_route = (*route, (node, neighbor, kind, relation, direction, strength))
            state = (neighbor, next_worst, next_depth)
            value = (next_is_a, next_route)
            if state not in best or value < best[state]:
                best[state] = value
                heapq.heappush(heap, (next_worst, next_depth, next_is_a, next_route, neighbor))

    by_node = {}
    for (node, worst, depth), (is_a_hops, route) in best.items():
        cost = (worst, depth, is_a_hops, route)
        if node not in by_node or cost < by_node[node]:
            by_node[node] = cost
    sources = sorted(
        (node for node in by_node if graph.nodes[node].get("has_why")),
        key=lambda node: (*by_node[node][:3], node),
    )
    candidates = []
    for node in sources[:max_candidates]:
        worst, depth, is_a_hops, route = by_node[node]
        candidates.append(
            {
                "node_id": node,
                "node_name": graph.nodes[node]["node_name"],
                "semantic_type": graph.nodes[node].get("semantic_type", "bfo_concept"),
                "depth": depth,
                "is_a_hops": is_a_hops,
                "path_strength": _RANK_STRENGTH[worst],
                "terminal_kind": "propagated" if route else "direct",
                "path": [
                    {
                        "hop": i,
                        "from_node_id": step[0],
                        "to_node_id": step[1],
                        "kind": step[2],
                        "relation_type": step[3],
                        "direction": step[4],
                        "strength": step[5],
                    }
                    for i, step in enumerate(route, 1)
                ],
                "why_dimension_hash": graph.nodes[node].get("why_dimension_hash"),
            }
        )
    return {
        "anchor_node_id": anchor_node_id,
        "candidates": candidates,
        "candidates_seen": len(sources),
        "visited_count": len(by_node),
        "truncated_by_depth": bool(depth_blocked - by_node.keys()),
        "truncated_by_candidates": len(sources) > max_candidates,
    }


def derive_bo_scope(
    graph: ConceptGraph, anchor_node_id: str, *, max_size: int = 5000
) -> dict[str, Any]:
    """Anchor plus every IS_A descendant; return the full scope even over budget."""
    _anchor(graph, anchor_node_id)
    _limit(max_size, 1, "作用域上限")
    seen, stack = {anchor_node_id}, [anchor_node_id]
    while stack:
        for child in graph.children.get(stack.pop(), ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    node_ids = sorted(seen)
    return {
        "node_ids": node_ids,
        "count": len(node_ids),
        "scope_hash": hashlib.sha256(canonical_json(node_ids).encode()).hexdigest(),
        "truncated": len(node_ids) > max_size,
    }


def path_strength(path: Iterable[Mapping[str, Any]]) -> str:
    worst = max((_STRENGTH_RANK.get(str(hop.get("strength")), 2) for hop in path), default=0)
    return _RANK_STRENGTH[worst]


def effective_priority(base_priority: Any, path: Iterable[Mapping[str, Any]]) -> str:
    base = base_priority if base_priority in _PRIORITY_ORDER else "中"
    weak_hops = sum(hop.get("strength") == "weak" for hop in path)
    return _PRIORITY_ORDER[min(_PRIORITY_ORDER.index(base) + weak_hops, 2)]


def path_summary(
    path: Iterable[Mapping[str, Any]], node_names: Mapping[str, str], *, anchor_node_id: str
) -> str:
    parts = [node_names.get(anchor_node_id, anchor_node_id)]
    for hop in path:
        node_id = str(hop.get("to_node_id"))
        arrow = (
            "is_a↑"
            if hop.get("kind") == "is_a_down"
            else f"{hop.get('relation_type')}({hop.get('direction')},{hop.get('strength')})"
        )
        parts.append(f"--{arrow}--> {node_names.get(node_id, node_id)}")
    return " ".join(parts)


def pack_prompt_evidence(
    items: list[dict[str, Any]], *, max_chars: int = 40_000
) -> list[dict[str, Any]]:
    """Pack deterministic evidence; mark any shortened item and honor JSON budget.

    Callers should compare the returned IDs with the input to expose any omitted
    sources. Original WHY content remains in the immutable source binding.
    """
    _limit(max_chars, 2, "证据字符预算")
    selected = []
    for item in items:
        if len(canonical_json([*selected, item])) <= max_chars:
            selected.append(deepcopy(item))
            continue
        compact = {
            key: item.get(key)
            for key in ("node_id", "node_name", "depth", "path_strength", "dimension_hash")
        }
        compact.update(why_excerpt="", truncated=True)
        if len(canonical_json([*selected, compact])) > max_chars:
            break
        serialized = canonical_json(item)
        low, high = 0, len(serialized)
        while low < high:
            mid = (low + high + 1) // 2
            compact["why_excerpt"] = serialized[:mid]
            if len(canonical_json([*selected, compact])) <= max_chars:
                low = mid
            else:
                high = mid - 1
        compact["why_excerpt"] = serialized[:low]
        selected.append(compact)
    return selected
