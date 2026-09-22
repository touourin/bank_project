"""WHY and typed topology from the same local, version-pinned ontology export."""

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from bank_project.alignment.catalog import Catalog
from bank_project.alignment.models import AlignmentError

from .propagation import ConceptGraph, canonical_json


def _object(value: Any) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("dimension container must be an object")
    return value


def _why(properties: dict) -> Any:
    """Read exported raw WHY; do not synthesize rules from names or metadata."""
    values = []
    if "why" in properties:
        values.append(properties["why"])
    for key in ("dimensions", "description"):
        if properties.get(key) is not None:
            container = _object(properties[key])
            if "why" in container:
                values.append(container["why"])
            if "dimensions" in container:
                nested = _object(container["dimensions"])
                if "why" in nested:
                    values.append(nested["why"])
    decoded = []
    for value in values:
        if isinstance(value, str):
            value = json.loads(value)
        if value is None or value == {} or value == []:
            continue
        if not isinstance(value, (dict, list)):
            raise ValueError("WHY must be an object or array")
        # Validate JSON numbers and conflicting duplicate projections before hashing.
        canonical_json(value)
        decoded.append(value)
    if any(value != decoded[0] for value in decoded[1:]):
        raise ValueError("conflicting WHY projections")
    return deepcopy(decoded[0]) if decoded else None


class RiskCatalog(Catalog):
    """Reuses alignment revision selection, lexical recall and file-size limits.

    Concept properties may embed ``why``, ``dimensions.why`` or
    ``description.why`` (objects or JSON strings). A provided WHY dimension hash
    must match its canonical raw JSON. Exports without WHY remain browsable.
    """

    def __init__(self, content: bytes, revision: str | None = None):
        super().__init__(content, revision)
        self.content = content
        try:
            raw = json.loads(content)["graph"]
            nodes, aliases = {}, {}
            self.why_by_id = {}
            for entry in raw["nodes"]:
                p = entry["properties"]
                if entry["label"] != "Concept" or p.get("dataset_revision") != self.revision:
                    continue
                key = p["node_id"]
                if not key or not p["node_name"]:
                    raise ValueError("empty concept identity")
                for alias in (entry.get("key"), f"Concept:{self.revision}:{key}", key):
                    if alias is not None:
                        if alias in aliases and aliases[alias] != key:
                            raise ValueError("ambiguous concept key")
                        aliases[alias] = key
                why = _why(p)
                digest = hashlib.sha256(canonical_json(why).encode()).hexdigest() if why else None
                supplied_hashes = _object(p.get("dimension_hashes", {}))
                supplied = supplied_hashes.get("why") or p.get("why_dimension_hash")
                if supplied and supplied != digest:
                    raise ValueError("WHY dimension hash does not match its content")
                if why:
                    self.why_by_id[key] = why
                nodes[key] = {
                    "node_id": key,
                    "node_name": p["node_name"],
                    "semantic_type": p.get("semantic_type")
                    or p.get("node_semantic_type")
                    or "bfo_concept",
                    "has_why": bool(why),
                    "why_dimension_hash": digest,
                    "dataset_revision": self.revision,
                }
            parents, children, rel_out, rel_in = (defaultdict(set) for _ in range(4))
            for edge in raw["relationships"]:
                if edge["properties"].get("dataset_revision") != self.revision:
                    continue
                # Exact exported keys prevent edges to another revision being
                # mistaken for this revision merely because node IDs match.
                if edge["start"] not in aliases or edge["end"] not in aliases:
                    raise ValueError("relationship leaves the pinned concept catalog")
                source, target = aliases[edge["start"]], aliases[edge["end"]]
                relation = edge["type"].lower()
                if relation == "is_a":
                    parents[source].add(target)
                    children[target].add(source)
                elif source != target:
                    rel_out[source].add((relation, target))
                    rel_in[target].add((relation, source))
            self.graph = ConceptGraph(
                nodes=nodes,
                parents={key: tuple(sorted(values)) for key, values in parents.items()},
                children={key: tuple(sorted(values)) for key, values in children.items()},
                rel_out={key: tuple(sorted(values)) for key, values in rel_out.items()},
                rel_in={key: tuple(sorted(values)) for key, values in rel_in.items()},
            )
            self.parent_ids = {key: set(values) for key, values in self.graph.parents.items()}
            self.parents = {
                key: [self.names[parent] for parent in values]
                for key, values in self.graph.parents.items()
            }
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise AlignmentError(
                "风险本体快照格式不合法：请检查 WHY 内容、哈希与版本内关系", 503
            ) from exc

    @classmethod
    def load(cls, path: Path, revision: str | None = None):
        return super().load(Path(path), revision)

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        details = super().search(query, limit)
        return [
            {
                **detail.model_dump(),
                "has_why": self.graph.nodes[detail.id]["has_why"],
            }
            for detail in details
        ]
