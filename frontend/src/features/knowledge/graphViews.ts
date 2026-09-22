import type { GraphData, QueryEvidence } from "./types";

/** Original company-graph traversal: ranked BFS or highest-degree induced core. */
export function selectGraph(
  graph: GraphData,
  mode: string,
  center: string,
  depth: number,
  limit: number,
  types: string[],
  evidence?: QueryEvidence,
) {
  const degrees = new Map(graph.nodes.map((n) => [n.id, 0]));
  for (const edge of graph.edges)
    for (const id of [edge.source, edge.target])
      degrees.set(id, (degrees.get(id) ?? 0) + 1);
  const degree = (id: string) =>
    Number(
      graph.nodes.find((n) => n.id === id)?.properties.degree ??
        degrees.get(id) ??
        0,
    );
  const allowed = new Set(
    graph.nodes
      .filter(
        (n) =>
          !types.length ||
          types.includes(n.type) ||
          (mode === "ego" && n.id === center),
      )
      .map((n) => n.id),
  );
  const rank = (p: Record<string, unknown>) =>
    Number(p.weight ?? 1) * Number(p.combined_degree ?? 1);
  const ranked = graph.edges
    .filter((e) => allowed.has(e.source) && allowed.has(e.target))
    .sort((a, b) => rank(b.properties) - rank(a.properties));
  let selected: string[] = [];
  if (mode === "ego" && allowed.has(center)) {
    selected = [center];
    const seen = new Set(selected);
    let frontier = new Set(selected);
    for (let d = 0; d < depth && selected.length < limit; d++) {
      const next = new Set<string>();
      for (const e of ranked.filter(
        (e) => frontier.has(e.source) || frontier.has(e.target),
      )) {
        const other = frontier.has(e.source) ? e.target : e.source;
        if (!seen.has(other) && selected.length < limit) {
          seen.add(other);
          selected.push(other);
          next.add(other);
        }
      }
      frontier = next;
      if (!next.size) break;
    }
  } else if (mode === "answer") {
    selected = (evidence?.node_ids ?? []).filter((id) => allowed.has(id));
    selected.sort(
      (a, b) =>
        Number(evidence?.cited_node_ids.includes(b)) -
          Number(evidence?.cited_node_ids.includes(a)) || degree(b) - degree(a),
    );
    selected = selected.slice(0, limit);
  } else {
    selected = graph.nodes
      .filter((n) => allowed.has(n.id))
      .sort((a, b) => degree(b.id) - degree(a.id))
      .slice(0, limit)
      .map((n) => n.id);
  }
  const ids = new Set(selected);
  return {
    nodes: graph.nodes.filter((n) => ids.has(n.id)),
    edges: ranked
      .filter(
        (e) =>
          ids.has(e.source) &&
          ids.has(e.target) &&
          e.source !== e.target &&
          (mode !== "answer" || evidence?.edge_ids.includes(e.id)),
      )
      .slice(0, 400),
    degrees,
  };
}
