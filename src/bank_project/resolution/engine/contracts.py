# Copyright (c) 2026 Microsoft Corporation.
# Licensed under the MIT License

"""Validated, model-independent inputs for paired resolution experiments."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

# Completed items, total items, failed requests, items skipped by the model budget.
ProgressCallback = Callable[[int, int, int, int], Awaitable[None]]
DecisionCallback = Callable[[dict], Awaitable[None]]


def require(condition: bool, message: str) -> None:
    """Reject invalid experiment data without silently dropping records."""
    if not condition:
        raise ValueError(message)


def text(value: Any, location: str, *, empty: bool = False) -> str:
    """Validate an explicit string field."""
    require(isinstance(value, str), f"{location} must be a string")
    require(empty or bool(value.strip()), f"{location} must not be empty")
    return value


def digest(value: Any) -> str:
    """Hash a canonical JSON representation."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def read_json(path) -> Any:
    """Read JSON while rejecting ambiguous duplicate object keys."""

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)


@dataclass(frozen=True)
class Identifier:
    """An upstream-reviewed identifier assertion backed by a literal quote."""

    namespace: str
    value: str
    quote: str
    verified: bool = False


@dataclass(frozen=True)
class Mention:
    """One raw extracted entity record or independently annotated mention."""

    mention_id: str
    name: str
    type: str
    source_id: str
    context: str
    description: str = ""
    aliases: tuple[str, ...] = ()
    identifiers: tuple[Identifier, ...] = ()
    source_span: tuple[int, int] | None = None
    evidence_kind: str = "source"

    def __post_init__(self) -> None:
        """Validate optional grounding without changing legacy record requirements."""
        require(
            self.evidence_kind in ("source", "catalog"),
            f"{self.mention_id}.evidence_kind must be source or catalog",
        )
        if self.source_span is None:
            return
        require(
            isinstance(self.source_span, tuple) and len(self.source_span) == 2,
            f"{self.mention_id}.source_span must be a pair of integer offsets",
        )
        start, end = self.source_span
        require(
            type(start) is int and type(end) is int,
            f"{self.mention_id}.source_span offsets must be integers",
        )
        require(
            isinstance(self.context, str) and 0 <= start < end <= len(self.context),
            f"{self.mention_id}.source_span is outside the source context",
        )
        require(
            self.context[start:end] == self.name,
            f"{self.mention_id}.source_span does not match the mention name",
        )


@dataclass(frozen=True)
class Constraint:
    """An explicitly reviewed identity constraint, never imported from gold."""

    left: str
    right: str
    verdict: str
    reason: str


@dataclass(frozen=True)
class Corpus:
    """The shared, frozen input of both resolution methods."""

    namespace: str
    mentions: tuple[Mention, ...]
    input_kind: str = "extracted_records"
    constraints: tuple[Constraint, ...] = ()
    relations: tuple[dict, ...] = ()
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize the public corpus schema."""
        data = {"schema_version": "er-corpus-v1", **asdict(self)}
        for row in data["mentions"]:
            # Preserve the canonical representation and hashes of legacy corpora.
            if row["source_span"] is None:
                del row["source_span"]
            if row["evidence_kind"] == "source":
                del row["evidence_kind"]
        data["constraints"] = [{**asdict(item), "reviewed": True} for item in self.constraints]
        return json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))

    @property
    def sha256(self) -> str:
        """Identify the exact shared input, including record order."""
        return digest(self.to_dict())

    @classmethod
    def from_dict(cls, data: Any) -> Corpus:
        """Validate evidence, IDs and references before any model invocation."""
        require(isinstance(data, dict), "Corpus must be an object")
        require(data.get("schema_version") == "er-corpus-v1", "Expected er-corpus-v1")
        require(
            not ({"gold", "labels", "gold_entities"} & data.keys()),
            "Keep gold labels in a separate file",
        )
        namespace = text(data.get("namespace"), "namespace")
        rows = data.get("mentions")
        require(isinstance(rows, list) and bool(rows), "mentions must be a nonempty list")
        mentions, seen = [], set()
        allowed = {
            "mention_id",
            "name",
            "type",
            "source_id",
            "context",
            "description",
            "aliases",
            "identifiers",
            "source_span",
            "evidence_kind",
        }
        for row in rows:
            require(isinstance(row, dict), "Mention must be an object")
            require(
                not (row.keys() - allowed),
                f"Unexpected mention fields: {row.keys() - allowed}",
            )
            mid = text(row.get("mention_id"), "mention_id")
            require(mid not in seen, f"Duplicate mention_id: {mid}")
            seen.add(mid)
            name = text(row.get("name"), f"{mid}.name")
            context = text(row.get("context"), f"{mid}.context")
            source_span = row.get("source_span")
            if source_span is not None:
                require(
                    isinstance(source_span, list) and len(source_span) == 2,
                    f"{mid}.source_span must be a pair of integer offsets",
                )
                source_span = tuple(source_span)
            aliases = row.get("aliases", [])
            require(isinstance(aliases, list), f"{mid}.aliases must be a list")
            for alias in aliases:
                text(alias, f"{mid}.alias")
            ids = row.get("identifiers", [])
            require(isinstance(ids, list), f"{mid}.identifiers must be a list")
            identifiers = []
            for assertion in ids:
                require(isinstance(assertion, dict), f"Invalid identifier for {mid}")
                identifier = Identifier(
                    namespace=text(assertion.get("namespace"), "identifier.namespace"),
                    value=text(assertion.get("value"), "identifier.value"),
                    quote=text(assertion.get("quote"), "identifier.quote"),
                    verified=assertion.get("verified", False),
                )
                require(
                    isinstance(identifier.verified, bool),
                    "identifier.verified must be a boolean",
                )
                require(
                    identifier.quote in context,
                    f"Identifier quote is absent from {mid}'s context",
                )
                require(
                    identifier.value in identifier.quote,
                    f"Identifier value is absent from {mid}'s quote",
                )
                identifiers.append(identifier)
            mentions.append(
                Mention(
                    mention_id=mid,
                    name=name,
                    type=text(row.get("type"), f"{mid}.type", empty=True),
                    source_id=text(row.get("source_id"), f"{mid}.source_id"),
                    context=context,
                    description=text(row.get("description", ""), f"{mid}.description", empty=True),
                    aliases=tuple(aliases),
                    identifiers=tuple(identifiers),
                    source_span=source_span,
                    evidence_kind=row.get("evidence_kind", "source"),
                )
            )
        constraints, pairs = [], set()
        for row in data.get("constraints", []):
            require(isinstance(row, dict), "Invalid constraint")
            left, right = row.get("left"), row.get("right")
            require(
                isinstance(left, str) and isinstance(right, str),
                "Constraint references must be strings",
            )
            require(
                left in seen and right in seen and left != right,
                "Invalid constraint references",
            )
            require(
                row.get("verdict") in ("same", "different"),
                "Invalid constraint verdict",
            )
            require(row.get("reviewed") is True, "Constraints must be explicitly reviewed")
            pair = tuple(sorted((left, right)))
            require(pair not in pairs, "Duplicate or contradictory constraint")
            pairs.add(pair)
            constraints.append(
                Constraint(
                    left,
                    right,
                    row["verdict"],
                    text(row.get("reason"), "constraint.reason"),
                )
            )
        relations, relation_ids = [], set()
        for row in data.get("relations", []):
            require(isinstance(row, dict), "Invalid relation")
            rid = text(row.get("relation_id"), "relation_id")
            require(rid not in relation_ids, "Duplicate relation_id")
            relation_ids.add(rid)
            for side in ("source_mention_id", "target_mention_id"):
                require(
                    isinstance(row.get(side), str) and row[side] in seen,
                    "Unknown relation endpoint",
                )
            relations.append(dict(row))
        kind = data.get("input_kind", "extracted_records")
        require(kind in ("extracted_records", "mentions"), "Invalid input_kind")
        require(isinstance(data.get("metadata", {}), dict), "metadata must be an object")
        return cls(
            namespace,
            tuple(mentions),
            kind,
            tuple(constraints),
            tuple(relations),
            data.get("metadata", {}),
        )


@dataclass(frozen=True)
class ResolverConfig:
    """Bound the pilot resolver's work and explicitly control model decisions."""

    candidate_limit: int = 30
    retrieval_policy: str = "original"
    max_block_size: int = 500
    max_pairs: int = 20_000
    max_model_calls: int = 2_000
    max_cluster_size: int = 100
    concurrency: int = 8
    embedding_neighbors: int = 10
    max_vector_records: int = 2_000
    timeout_seconds: float = 60
    model_policy: str = "review"
    unique_id_namespaces: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject ambiguous or unbounded operational settings."""
        require(self.retrieval_policy in ("original", "balanced"), "Unknown retrieval policy")
        for name in (
            "candidate_limit",
            "max_block_size",
            "max_pairs",
            "max_model_calls",
            "max_cluster_size",
            "concurrency",
            "embedding_neighbors",
            "max_vector_records",
        ):
            value = getattr(self, name)
            require(
                isinstance(value, int) and not isinstance(value, bool) and value > 0,
                f"{name} must be a positive integer",
            )
        require(
            isinstance(self.timeout_seconds, (int, float))
            and not isinstance(self.timeout_seconds, bool)
            and 0 < self.timeout_seconds <= 600,
            "timeout_seconds must be in (0, 600]",
        )
        require(
            self.model_policy in ("review", "apply"),
            "model_policy must be review or apply",
        )
        require(
            isinstance(self.unique_id_namespaces, (list, tuple)),
            "unique_id_namespaces must be a sequence",
        )
        for value in self.unique_id_namespaces:
            text(value, "unique_id_namespace")


@dataclass
class Resolution:
    """Complete memberships, candidates and decisions from one method."""

    method: str
    corpus_sha256: str
    memberships: list[dict]
    entities: list[dict]
    decisions: list[dict] = field(default_factory=list)
    candidates: dict[str, list[str]] = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    relations: list[dict] = field(default_factory=list)
