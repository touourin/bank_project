"""Load one explicit ready revision; never mix distinct ontology datasets."""

import hashlib
import json
import re
import unicodedata
from pathlib import Path

from bank_project.ontology.dimensions import read_dimension

from .models import AlignmentError, ConceptDetail, ConceptRef


class Catalog:
    def __init__(self, content: bytes, revision: str | None = None):
        try:
            document = json.loads(content)
            graph = document["graph"]
            self.content = content
            self.source = document.get("ontology_source", {})
            self.ontology_id = self.source.get("ontology_id")
            ready = {
                n["properties"]["revision"]
                for n in graph["nodes"]
                if n["label"] == "OntologyDataset" and n["properties"].get("status") == "ready"
            }
            if revision is None:
                if len(ready) != 1:
                    raise AlignmentError(
                        "本体快照含多个可用版本，请配置 BANK_ONTOLOGY_REVISION", 503
                    )
                revision = next(iter(ready))
            if revision not in ready:
                raise AlignmentError("配置的本体版本不存在或尚未就绪", 503)
            self.revision, self.sha256 = revision, hashlib.sha256(content).hexdigest()
            self.names = {}
            self.metadata = {}
            self.why_ids = set()
            aliases = {}
            for n in graph["nodes"]:
                p = n["properties"]
                if n["label"] == "Concept" and p.get("dataset_revision") == revision:
                    key, name = p["node_id"], p["node_name"]
                    if not isinstance(key, str) or not isinstance(name, str) or key in self.names:
                        raise ValueError("invalid concept")
                    self.names[key] = name
                    self.metadata[key] = p
                    if "why" in p.get("nonempty_dimensions", []) or read_dimension(p, "why"):
                        self.why_ids.add(key)
                    for alias in (n.get("key"), f"Concept:{revision}:{key}", key):
                        if alias is not None:
                            if alias in aliases and aliases[alias] != key:
                                raise ValueError("ambiguous concept key")
                            aliases[alias] = key
            self.parents: dict[str, list[str]] = {}
            self.parent_ids: dict[str, set[str]] = {}
            self.relations = []
            for edge in graph["relationships"]:
                if edge["properties"].get("dataset_revision") != revision:
                    continue
                if edge["start"] not in aliases or edge["end"] not in aliases:
                    raise ValueError("relationship leaves pinned ontology")
                start, end = aliases[edge["start"]], aliases[edge["end"]]
                if start in self.names and end in self.names:
                    self.relations.append({"source": start, "target": end, "type": edge["type"]})
                if (
                    edge["type"] == "IS_A"
                    and edge["properties"].get("dataset_revision") == revision
                ):
                    if start in self.names and end in self.names:
                        self.parents.setdefault(start, []).append(self.names[end])
                        self.parent_ids.setdefault(start, set()).add(end)
            if not self.names:
                raise ValueError("empty catalog")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise AlignmentError("本体快照格式不合法，无法进行匹配", 503) from exc

    @classmethod
    def load(cls, path: Path, revision: str | None = None):
        try:
            if path.stat().st_size > 30 * 1024 * 1024:
                raise AlignmentError("本体快照超过 30 MB 上限", 503)
            return cls(path.read_bytes(), revision)
        except OSError as exc:
            raise AlignmentError("未找到可读取的本地本体快照，请检查本体配置", 503) from exc

    def candidates(self, terms: list[str], limit: int = 60) -> list[str]:
        """Local lexical recall using model-generated Chinese meanings and original labels."""

        def normalize(text):
            return re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", text).lower())

        queries = {normalize(t) for t in terms if t}
        queries.discard("")

        def score(name):
            normalized = normalize(name)
            pairs = {normalized[i : i + 2] for i in range(len(normalized) - 1)}
            best = 0
            for query in queries:
                if normalized == query:
                    best = max(best, 100)
                elif min(len(query), len(normalized)) >= 2 and (
                    query in normalized or normalized in query
                ):
                    best = max(
                        best,
                        40
                        + 20 * min(len(query), len(normalized)) / max(len(query), len(normalized)),
                    )
                else:
                    query_pairs = {query[i : i + 2] for i in range(len(query) - 1)}
                    common = pairs & query_pairs
                    if common:
                        best = max(best, 20 * len(common) / len(pairs | query_pairs))
            return best

        scored = [(score(name), key) for key, name in self.names.items()]
        return [
            key for value, key in sorted(scored, key=lambda item: (-item[0], item[1])) if value > 0
        ][:limit]

    def prompt(self, candidates: list[str] | None = None) -> str:
        text = json.dumps(
            [
                {"id": key, "name": name, "parents": self.parents.get(key, [])}
                for key, name in sorted(self.names.items())
                if candidates is None or key in candidates
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(text) > 500_000:
            raise AlignmentError("本体目录过大，请缩小所选本体版本", 503)
        return text

    def relation_prompt(self, candidates: list[str]) -> str:
        """Only explicit catalog edges, never inferred multi-hop business facts."""
        keys = set(candidates)
        return json.dumps(
            [r for r in self.relations if r["source"] in keys and r["target"] in keys][:400],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def describe(self, concept_id: str) -> ConceptDetail:
        if concept_id not in self.names:
            raise AlignmentError("该节点不在本次分析的本体版本中")
        return ConceptDetail(
            id=concept_id,
            name=self.names[concept_id],
            semantic_type=self.metadata[concept_id].get("node_semantic_type")
            or self.metadata[concept_id].get("semantic_type"),
            has_why=concept_id in self.why_ids,
            parents=[
                ConceptRef(id=key, name=self.names[key])
                for key in sorted(self.parent_ids.get(concept_id, set()))
            ],
        )

    def search(self, query: str, limit: int = 50) -> list[ConceptDetail]:
        query = query.strip()
        if query in self.names:
            keys = [query]
        elif query:
            keys = self.candidates([query], limit)
        else:
            keys = sorted(self.names, key=lambda key: (self.names[key], key))[:limit]
        return [self.describe(key) for key in keys]
