"""Graph selection and citation mapping migrated from the original company graph page."""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pandas as pd

CITATION_RE = re.compile(r"(Entities|Relationships|Reports|Sources|Documents)\s*\(([^)]*)\)")


def _entity_lookup(entities: pd.DataFrame) -> pd.DataFrame:
    """Entities indexed by title with only the columns the graph needs."""
    df = pd.DataFrame(
        {
            "title": entities["title"].astype(str),
            "type": entities["type"].fillna("").astype(str) if "type" in entities else "",
            "description": entities["description"].fillna("").astype(str)
            if "description" in entities
            else "",
            "degree": pd.to_numeric(entities.get("degree", 0), errors="coerce")
            .fillna(0)
            .astype(int),
            "id": entities["id"].astype(str) if "id" in entities else "",
        }
    )
    df = df.drop_duplicates("title").set_index("title", drop=False)
    df.index.name = None  # keep "title" usable as a column in sort_values/loc
    return df


def _ranked_relationships(relationships: pd.DataFrame) -> pd.DataFrame:
    """Relationships with string endpoints and a rank used to pick the most informative edges."""
    rels = pd.DataFrame(
        {
            "human_readable_id": pd.to_numeric(
                relationships.get("human_readable_id", -1), errors="coerce"
            )
            .fillna(-1)
            .astype(int),
            "source": relationships["source"].astype(str),
            "target": relationships["target"].astype(str),
            "description": relationships["description"].fillna("").astype(str)
            if "description" in relationships
            else "",
            "weight": pd.to_numeric(relationships.get("weight", 1.0), errors="coerce").fillna(0.0),
            "combined_degree": pd.to_numeric(
                relationships.get("combined_degree", 1.0), errors="coerce"
            ).fillna(1.0),
        }
    )
    rels["rank"] = rels["weight"] * rels["combined_degree"]
    return rels


def _induced_edges(rels: pd.DataFrame, titles: set[str], max_edges: int = 400) -> pd.DataFrame:
    edges = rels[rels["source"].isin(titles) & rels["target"].isin(titles)]
    edges = edges[edges["source"] != edges["target"]]
    return edges.sort_values("rank", ascending=False).head(max_edges)


def build_ego_subgraph(
    lookup: pd.DataFrame,
    rels: pd.DataFrame,
    center: str,
    depth: int,
    max_nodes: int,
    allowed_types: list[str] | None,
) -> tuple[list[str], pd.DataFrame]:
    """Breadth-first expansion from ``center`` following the highest-ranked relationships first."""
    if allowed_types:
        keep = set(lookup.index[lookup["type"].isin(allowed_types)]) | {center}
        rels = rels[rels["source"].isin(keep) & rels["target"].isin(keep)]
    selected = [center]
    seen = {center}
    frontier = {center}
    for _ in range(depth):
        candidates = rels[
            rels["source"].isin(frontier) | rels["target"].isin(frontier)
        ].sort_values("rank", ascending=False)
        next_frontier: set[str] = set()
        for row in candidates.itertuples(index=False):
            other = row.target if row.source in frontier else row.source
            if other in seen:
                continue
            if len(selected) >= max_nodes:
                break
            seen.add(other)
            selected.append(other)
            next_frontier.add(other)
        frontier = next_frontier
        if not frontier or len(selected) >= max_nodes:
            break
    return selected, _induced_edges(rels, seen)


def build_core_subgraph(
    lookup: pd.DataFrame,
    rels: pd.DataFrame,
    top_n: int,
    allowed_types: list[str] | None,
) -> tuple[list[str], pd.DataFrame]:
    """The ``top_n`` best-connected entities and every relationship among them."""
    pool = lookup if not allowed_types else lookup[lookup["type"].isin(allowed_types)]
    titles = pool.sort_values("degree", ascending=False).head(top_n)["title"].tolist()
    return titles, _induced_edges(rels, set(titles))


def cited_ids(response: str) -> dict[str, set[str]]:
    """Ids cited inline by GraphRAG answers, e.g. ``[Data: Entities (12, 45); Reports (3)]``."""
    found: dict[str, set[str]] = {}
    for key, body in CITATION_RE.findall(response or ""):
        ids = {part.strip() for part in body.split(",") if part.strip().isdigit()}
        found.setdefault(key.lower(), set()).update(ids)
    return found


def _context_frame(context: Any, key: str) -> pd.DataFrame:
    """One table of the search context, restricted to rows that made it into the prompt."""
    if not isinstance(context, dict):
        return pd.DataFrame()
    value = next((v for k, v in context.items() if str(k).lower() == key.lower()), None)
    if value is None:
        return pd.DataFrame()
    df = value if isinstance(value, pd.DataFrame) else pd.DataFrame(value)
    if "in_context" in df.columns:
        flags = df["in_context"].astype(str).str.lower().isin(["true", "1"])
        df = df[flags]
    return df


def _entities_from_reports(
    sv: Any,
    report_ids: set[str],
    cited_report_ids: set[str],
    lookup: pd.DataFrame,
) -> tuple[list[str], set[str]]:
    """Global search only cites community reports; map them back to the communities' entities."""
    reports = sv.community_reports.value
    communities = sv.communities.value
    entities = sv.entities.value
    if reports is None or communities is None or reports.empty or communities.empty:
        return [], set()
    rows = reports[reports["human_readable_id"].astype(str).isin(report_ids)]
    cited_communities = set(
        rows.loc[rows["human_readable_id"].astype(str).isin(cited_report_ids), "community"]
    )
    id_to_title = entities.set_index(entities["id"].astype(str))["title"].astype(str)
    titles: list[str] = []
    cited: set[str] = set()
    for row in communities[communities["community"].isin(set(rows["community"]))].itertuples():
        entity_ids = [] if row.entity_ids is None else list(row.entity_ids)
        for entity_id in entity_ids:
            title = id_to_title.get(str(entity_id))
            if title is None or title not in lookup.index:
                continue
            titles.append(title)
            if row.community in cited_communities:
                cited.add(title)
    return titles, cited


def build_answer_subgraph(
    sv: Any,
    lookup: pd.DataFrame,
    rels: pd.DataFrame,
    message: dict[str, Any],
    max_nodes: int,
) -> tuple[list[str], pd.DataFrame, set[str], set[int]]:
    """Entities/relationships the answer was grounded on; directly cited ones are returned separately."""
    context = message.get("context") or {}
    cited = cited_ids(message.get("content", ""))

    titles: list[str] = []
    cited_titles: set[str] = set()
    entity_ctx = _context_frame(context, "entities")
    if not entity_ctx.empty and "entity" in entity_ctx.columns:
        names = entity_ctx["entity"].astype(str)
        titles = [name for name in names if name in lookup.index]
        if "id" in entity_ctx.columns:
            mask = entity_ctx["id"].astype(str).isin(cited.get("entities", set()))
            cited_titles = set(names[mask]) & set(titles)
    if not titles:
        report_ctx = _context_frame(context, "reports")
        if not report_ctx.empty and "id" in report_ctx.columns:
            titles, cited_titles = _entities_from_reports(
                sv,
                set(report_ctx["id"].astype(str)),
                cited.get("reports", set()),
                lookup,
            )

    cited_edge_ids = {int(x) for x in cited.get("relationships", set())}
    if cited_edge_ids:
        # keep both endpoints of every relationship the answer cites, even if the entity table
        # did not list them (local search includes "out-of-network" relationships)
        cited_rel_rows = rels[rels["human_readable_id"].isin(cited_edge_ids)]
        for endpoint in pd.concat([cited_rel_rows["source"], cited_rel_rows["target"]]):
            if endpoint in lookup.index:
                titles.append(endpoint)

    titles = list(dict.fromkeys(titles))
    titles.sort(key=lambda t: (t not in cited_titles, -int(lookup.at[t, "degree"])))
    titles = titles[:max_nodes]
    title_set = set(titles)

    rel_ctx = _context_frame(context, "relationships")
    if not rel_ctx.empty and {"source", "target"} <= set(rel_ctx.columns):
        pairs = set(zip(rel_ctx["source"].astype(str), rel_ctx["target"].astype(str), strict=True))
        in_ctx = [
            (s, t) in pairs or (t, s) in pairs
            for s, t in zip(rels["source"], rels["target"], strict=True)
        ]
        edges = rels[in_ctx]
        edges = edges[edges["source"].isin(title_set) & edges["target"].isin(title_set)]
        if edges.empty:
            edges = _induced_edges(rels, title_set)
    else:
        edges = _induced_edges(rels, title_set)
    return titles, edges, cited_titles, cited_edge_ids


def answer_evidence(tables, answer, context):
    sv = SimpleNamespace(**{key: SimpleNamespace(value=value) for key, value in tables.items()})
    lookup = _entity_lookup(tables["entities"])
    rels = _ranked_relationships(tables["relationships"])
    titles, edges, cited, edge_citations = build_answer_subgraph(
        sv, lookup, rels, {"content": answer, "context": context}, len(lookup)
    )
    # Basic search cites source chunks rather than entity/report rows.
    sources = _context_frame(context, "sources")
    source_column = "id" if "id" in sources else "source_id"
    if not titles and not sources.empty and source_column in sources:
        units = tables["text_units"]
        ids = set(sources[source_column].astype(str))
        units = units[units["human_readable_id"].astype(str).isin(ids)]
        entity_ids = {str(e) for row in units.get("entity_ids", []) if row is not None for e in row}
        titles = lookup[lookup["id"].isin(entity_ids)]["title"].tolist()
        cited_sources = cited_ids(answer).get("sources", set())
        direct = units[units["human_readable_id"].astype(str).isin(cited_sources)]
        direct_ids = {
            str(e) for row in direct.get("entity_ids", []) if row is not None for e in row
        }
        cited.update(lookup[lookup["id"].isin(direct_ids)]["title"].tolist())
        edges = _induced_edges(rels, set(titles))
    relationship_ids = set(edges["human_readable_id"])
    return {
        "node_ids": [str(lookup.at[t, "id"]) for t in titles],
        "cited_node_ids": [str(lookup.at[t, "id"]) for t in sorted(cited)],
        "edge_ids": [
            str(r.id)
            for r in tables["relationships"].itertuples()
            if r.human_readable_id in relationship_ids
        ],
        "cited_edge_ids": [
            str(r.id)
            for r in tables["relationships"].itertuples()
            if r.human_readable_id in edge_citations
        ],
    }
