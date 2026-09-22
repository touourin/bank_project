"""Decode dimension projections in current and legacy ontology exports."""

import json
from copy import deepcopy
from typing import Any

from .serialization import canonical_json


def object_value(value: Any) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("dimension container must be an object")
    return value


def read_dimension(properties: dict, name: str) -> Any:
    """Read a raw dimension without synthesizing content from labels or metadata."""
    values = []
    if name in properties:
        values.append(properties[name])
    for key in ("dimensions", "description"):
        if properties.get(key) is not None:
            container = object_value(properties[key])
            if name in container:
                values.append(container[name])
            if "dimensions" in container:
                nested = object_value(container["dimensions"])
                if name in nested:
                    values.append(nested[name])
    decoded = []
    for value in values:
        if isinstance(value, str):
            value = json.loads(value)
        if value is None or value == {} or value == []:
            continue
        if not isinstance(value, (dict, list)):
            raise ValueError("Dimension must be an object or array")
        # Validate JSON numbers and conflicting duplicate projections before hashing.
        canonical_json(value)
        decoded.append(value)
    if any(value != decoded[0] for value in decoded[1:]):
        raise ValueError("conflicting dimension projections")
    return deepcopy(decoded[0]) if decoded else None
