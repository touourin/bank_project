import type { ElementDefinition } from "cytoscape";
import type { GraphGroup, GraphNodeBrief, GraphPage } from "./types";

export type GraphMode = "classification" | "business";

export function conceptColor(id: string) {
  const palette = [
    "#7fc5a6",
    "#c5ba64",
    "#7db8d4",
    "#b3a1db",
    "#dba88a",
    "#94b97e",
  ];
  let hash = 0;
  for (const c of id) hash = ((hash << 5) - hash + c.charCodeAt(0)) | 0;
  return palette[(hash >>> 0) % palette.length];
}

/** Membership edges exist only in this view; never send them to graph storage. */
export function graphElements(
  nodes: GraphNodeBrief[],
  groups: GraphGroup[],
  edges: GraphPage["edges"],
  mode: GraphMode,
): ElementDefinition[] {
  const elements: ElementDefinition[] = [];
  const types = new Map(groups.map((g, i) => [g.concept_id, i]));
  if (mode === "classification") {
    for (const group of groups) {
      const i = types.get(group.concept_id)!;
      elements.push({
        data: {
          id: `type:${group.concept_id}`,
          concept: group.concept_id,
          label: `${group.concept_name}\n${group.count.toLocaleString()} 个实例`,
          kind: "type",
          color: conceptColor(group.concept_id),
          size: 70 + Math.min(20, Math.log10(group.count + 1) * 5),
        },
        position: { x: (i % 4) * 420, y: Math.floor(i / 4) * 420 },
      });
    }
  }
  const localCounts = new Map<string, number>();
  for (const [index, node] of nodes.entries()) {
    const group = types.get(node.concept_id) ?? 0;
    const ordinal = localCounts.get(node.concept_id) ?? 0;
    localCounts.set(node.concept_id, ordinal + 1);
    const angle = (mode === "classification" ? ordinal : index) * 2.39996;
    const radius = 90 + 14 * Math.sqrt(ordinal + 1);
    elements.push({
      data: {
        id: `instance:${node.id}`,
        instance: node.id,
        label: node.name,
        kind: "instance",
        color: conceptColor(node.concept_id),
        size: 13,
      },
      position: {
        x: (group % 4) * 420 + radius * Math.cos(angle),
        y: Math.floor(group / 4) * 420 + radius * Math.sin(angle),
      },
    });
    if (mode === "classification" && types.has(node.concept_id)) {
      elements.push({
        data: {
          id: `membership:${node.id}`,
          source: `type:${node.concept_id}`,
          target: `instance:${node.id}`,
          label: "分类归属（展示）",
          kind: "membership",
        },
      });
    }
  }
  if (mode === "business") {
    const ids = new Set(nodes.map((n) => n.id));
    for (const [i, edge] of edges.entries()) {
      if (ids.has(edge.source) && ids.has(edge.target))
        elements.push({
          data: {
            id: `relation:${i}`,
            source: `instance:${edge.source}`,
            target: `instance:${edge.target}`,
            label: edge.name || "业务关联",
            kind: edge.origin === "candidate" ? "candidate" : "business",
          },
        });
    }
  }
  return elements;
}
