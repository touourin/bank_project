"""Stable UTF-8 encoding shared by ontology evidence and WHY provenance."""

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def dimension_hash(value: Any) -> str | None:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest() if value else None
