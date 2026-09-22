"""Versioned WHY input and deterministic applicability (not business inference)."""

import hashlib
import json

import pytest

from bank_project.alignment.models import AlignmentError
from bank_project.risk.catalog import RiskCatalog
from bank_project.risk.propagation import (
    canonical_json,
    derive_bo_scope,
    effective_priority,
    pack_prompt_evidence,
    path_strength,
    path_summary,
    propagate,
)

WHY = {"why_id": "cash-limit", "rule_name": "现金累计限额", "priority": "高"}


def catalog(nodes, edges=(), revision="r1", extra_nodes=(), extra_edges=()):
    """nodes: ID, name, properties; edges: source, relation, target."""
    content = {
        "graph": {
            "nodes": [
                {
                    "label": "OntologyDataset",
                    "properties": {"revision": revision, "status": "ready"},
                },
                *[
                    {
                        "label": "Concept",
                        "key": f"Concept:{revision}:{key}",
                        "properties": {
                            "node_id": key,
                            "node_name": name,
                            "dataset_revision": revision,
                            **props,
                        },
                    }
                    for key, name, props in nodes
                ],
                *extra_nodes,
            ],
            "relationships": [
                *[
                    {
                        "start": f"Concept:{revision}:{source}",
                        "end": f"Concept:{revision}:{target}",
                        "type": relation,
                        "properties": {"dataset_revision": revision},
                    }
                    for source, relation, target in edges
                ],
                *extra_edges,
            ],
        }
    }
    return RiskCatalog(json.dumps(content, ensure_ascii=False).encode(), revision)


def sources(result):
    return {item["node_id"]: item for item in result["candidates"]}


def test_cash_limit_example_preserves_source_but_binds_only_anchor_descendants():
    c = catalog(
        [
            ("limit", "现金存入限额", {"why": WHY}),
            ("cash", "现金存入", {}),
            ("large", "大额现金存入", {}),
        ],
        [("limit", "INHERES_IN", "cash"), ("large", "IS_A", "cash")],
    )
    result = propagate(c.graph, "cash")
    rule = sources(result)["limit"]
    assert rule["path_strength"] == "strong"
    assert rule["depth"] == 1
    assert rule["path"][0]["direction"] == "forward"
    assert c.why_by_id["limit"] == WHY
    assert rule["why_dimension_hash"] == hashlib.sha256(canonical_json(WHY).encode()).hexdigest()
    assert c.graph.nodes["limit"]["dataset_revision"] == "r1"
    assert c.sha256 == hashlib.sha256(c.content).hexdigest()
    scope = derive_bo_scope(c.graph, "cash")
    assert scope["node_ids"] == ["cash", "large"]
    assert not scope["truncated"]
    assert c.search("限额")[0]["has_why"] is True


def test_all_parents_inherit_but_children_do_not():
    c = catalog(
        [
            ("a", "锚点", {}),
            ("p1", "父一", {"why": WHY}),
            ("p2", "父二", {"why": WHY}),
            ("child", "子类", {"why": WHY}),
        ],
        [("a", "IS_A", "p2"), ("a", "IS_A", "p1"), ("child", "IS_A", "a")],
    )
    result = sources(propagate(c.graph, "a", max_depth=0))
    assert list(result) == ["p1", "p2"]
    assert all(rule["path_strength"] == "deductive" for rule in result.values())
    assert all(rule["is_a_hops"] == 1 and rule["depth"] == 0 for rule in result.values())
    assert c.search("a")[0]["parents"] == [
        {"id": "p1", "name": "父一"},
        {"id": "p2", "name": "父二"},
    ]


@pytest.mark.parametrize(
    "relation, forward, reverse",
    [
        ("inheres_in", "strong", None),
        ("has_participant", "strong", "weak"),
        ("preceded_by", None, "weak"),
        ("continuant_part_of", "strong", None),
        ("occurrent_part_of", "strong", None),
        ("located_in", None, "strong"),
        ("derives_from", None, "strong"),
        ("adjacent_to", None, None),
        ("unknown_relation", None, None),
    ],
)
def test_explicit_direction_policy(relation, forward, reverse):
    c = catalog([("s", "源", {"why": WHY}), ("t", "终点", {"why": WHY})], [("s", relation, "t")])
    from_target = sources(propagate(c.graph, "t"))
    from_source = sources(propagate(c.graph, "s"))
    assert from_target.get("s", {}).get("path_strength") == forward
    assert from_source.get("t", {}).get("path_strength") == reverse
    assert from_target["t"]["terminal_kind"] == "direct"


def test_weaker_shorter_prefix_survives_depth_budget():
    # a -> x -> middle is strong but spends both relation hops. The weak
    # a -> middle prefix still has one hop to reach WHY; node-only best loses it.
    c = catalog(
        [
            ("a", "锚点", {}),
            ("x", "中转", {}),
            ("middle", "汇合点", {}),
            ("why", "WHY", {"why": WHY}),
        ],
        [
            ("x", "inheres_in", "a"),
            ("middle", "inheres_in", "x"),
            ("a", "has_participant", "middle"),
            ("why", "inheres_in", "middle"),
        ],
    )
    result = sources(propagate(c.graph, "a", max_depth=2))
    assert result["why"]["depth"] == 2
    assert result["why"]["path_strength"] == "weak"
    assert [hop["to_node_id"] for hop in result["why"]["path"]] == ["middle", "why"]


def test_strength_tie_after_weak_edge_keeps_shorter_inheritance_prefix():
    # Strong route to m has two IS_A hops; weak route to m has none. A final
    # weak hop equalizes strength, so the latter must become the preferred path.
    c = catalog(
        [
            ("a", "a", {}),
            ("p", "p", {}),
            ("q", "q", {}),
            ("m", "m", {}),
            ("w", "w", {"why": WHY}),
        ],
        [
            ("a", "IS_A", "p"),
            ("p", "IS_A", "q"),
            ("m", "inheres_in", "q"),
            ("a", "preceded_by", "m"),
            ("m", "preceded_by", "w"),
        ],
    )
    rule = sources(propagate(c.graph, "a", max_depth=2))["w"]
    assert rule["is_a_hops"] == 0
    assert [hop["to_node_id"] for hop in rule["path"]] == ["m", "w"]


def test_deterministic_equal_paths_and_explicit_limits():
    nodes = [
        ("a", "a", {}),
        ("p", "p", {}),
        ("q", "q", {}),
        ("w", "w", {"why": WHY}),
        ("z", "z", {"why": WHY}),
    ]
    edges = [
        ("a", "IS_A", "q"),
        ("a", "IS_A", "p"),
        ("w", "inheres_in", "p"),
        ("w", "inheres_in", "q"),
        ("z", "inheres_in", "w"),
    ]
    first = catalog(nodes, edges)
    shuffled = catalog(list(reversed(nodes)), list(reversed(edges)))
    assert propagate(first.graph, "a") == propagate(shuffled.graph, "a")
    limited = propagate(first.graph, "a", max_depth=1)
    assert limited["truncated_by_depth"] is True
    assert set(sources(limited)) == {"w"}
    capped = propagate(first.graph, "a", max_candidates=1)
    assert capped["truncated_by_candidates"] is True
    assert capped["candidates_seen"] == 2
    assert [hop["to_node_id"] for hop in capped["candidates"][0]["path"]] == ["p", "w"]


def test_cycles_terminate_and_scope_is_never_partially_returned():
    c = catalog(
        [(key, key, {"why": WHY}) for key in ("a", "b", "c")],
        [
            ("a", "IS_A", "b"),
            ("b", "IS_A", "a"),
            ("c", "IS_A", "b"),
            ("a", "has_participant", "b"),
            ("b", "has_participant", "c"),
            ("c", "has_participant", "a"),
        ],
    )
    result = propagate(c.graph, "a")
    assert result["visited_count"] == 3
    assert result["truncated_by_depth"] is False
    assert sources(result)["a"]["path"] == []
    scope = derive_bo_scope(c.graph, "a", max_size=2)
    assert scope["node_ids"] == ["a", "b", "c"]
    assert scope["count"] == 3 and scope["truncated"] is True


def test_invalid_anchors_and_limits_raise_domain_error():
    c = catalog([("a", "a", {})])
    for operation in (propagate, derive_bo_scope):
        with pytest.raises(AlignmentError):
            operation(c.graph, "missing")
    for kwargs in ({"max_depth": -1}, {"max_depth": True}, {"max_candidates": 0}):
        with pytest.raises(AlignmentError):
            propagate(c.graph, "a", **kwargs)
    with pytest.raises(AlignmentError):
        derive_bo_scope(c.graph, "a", max_size=0)


def test_priority_and_evidence_budget_use_real_path():
    path = [{"strength": "deductive"}, {"strength": "weak"}, {"strength": "weak"}]
    assert path_strength(path) == "weak"
    assert path_strength([]) == "deductive"
    assert effective_priority("高", path) == "低"
    assert effective_priority("低", path) == "低"
    assert effective_priority("bad", []) == "中"
    c = catalog([("a", "现金", {}), ("w", "限额", {"why": WHY})], [("w", "inheres_in", "a")])
    route = sources(propagate(c.graph, "a"))["w"]["path"]
    assert (
        path_summary(route, c.names, anchor_node_id="a")
        == "现金 --inheres_in(forward,strong)--> 限额"
    )
    items = [{"node_id": "a", "why": WHY}, {"node_id": "w", "why": '"\\\n' * 10000}]
    packed = pack_prompt_evidence(items, max_chars=600)
    assert packed[0] == items[0]
    assert packed[1]["truncated"] is True
    assert len(canonical_json(packed)) <= 600


@pytest.mark.parametrize(
    "properties",
    [
        {"why": WHY},
        {"why": json.dumps(WHY)},
        {"dimensions": {"why": WHY}},
        {"description": json.dumps({"why": WHY})},
        {"description": {"dimensions": {"why": WHY}}},
    ],
)
def test_catalog_accepts_explicit_why_export_fields(properties):
    c = catalog([("a", "现金", properties)])
    assert c.why_by_id == {"a": WHY}


def test_catalog_validates_hashes_and_conflicting_content():
    good = hashlib.sha256(canonical_json(WHY).encode()).hexdigest()
    assert catalog([("a", "a", {"why": WHY, "dimension_hashes": {"why": good}})]).why_by_id
    for props in (
        {"why": WHY, "dimension_hashes": {"why": "0" * 64}},
        {"why": WHY, "dimensions": {"why": {"different": True}}},
        {"why": "unparseable"},
        {"why": 12},
        {"why": {"bad": float("nan")}},
    ):
        with pytest.raises(AlignmentError):
            catalog([("a", "a", props)])


def test_catalog_never_borrows_same_id_from_another_revision():
    other = [
        {"label": "OntologyDataset", "properties": {"revision": "r2", "status": "ready"}},
        {
            "label": "Concept",
            "key": "Concept:r2:a",
            "properties": {
                "node_id": "a",
                "node_name": "别的版本",
                "dataset_revision": "r2",
                "why": WHY,
            },
        },
    ]
    c = catalog([("a", "现金", {})], extra_nodes=other)
    assert c.names == {"a": "现金"} and c.why_by_id == {}
    assert RiskCatalog(c.content, "r2").why_by_id == {"a": WHY}
    with pytest.raises(AlignmentError):
        RiskCatalog(c.content)
    with pytest.raises(AlignmentError):
        catalog(
            [("a", "现金", {})],
            extra_nodes=other,
            extra_edges=[
                {
                    "start": "Concept:r1:a",
                    "end": "Concept:r2:a",
                    "type": "IS_A",
                    "properties": {"dataset_revision": "r1"},
                }
            ],
        )


def test_topology_only_export_does_not_invent_why(tmp_path):
    original = catalog(
        [("cash", "现金限额", {}), ("limit", "大额现金存入限额", {})],
        [("limit", "INHERES_IN", "cash")],
    )
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(original.content)
    c = RiskCatalog.load(snapshot, "r1")
    assert c.names and not c.why_by_id
    assert not any(item["has_why"] for item in c.search("", 20))
