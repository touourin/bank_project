import { request } from "../../api/request";
import type { GraphData } from "../knowledge/types";

export interface OntologyGraph {
  source: {
    kind: "remote" | "local";
    ontology_id: string | null;
    revision: string;
    snapshot_sha256: string;
    node_count: number;
    relation_count: number;
    why_node_count: number;
  };
  graph: GraphData;
}

const root = "/api/v1/ontology";
export const ontologyApi = {
  graph: (token: string, signal: AbortSignal) =>
    request<OntologyGraph>(`${root}/graph`, token, { signal }),
  dimensions: (
    token: string,
    source: OntologyGraph["source"],
    nodeId: string,
    signal: AbortSignal,
  ) =>
    request<Record<string, unknown>>(
      `${root}/concepts/${encodeURIComponent(nodeId)}/dimensions?${new URLSearchParams({ revision: source.revision, snapshot_sha256: source.snapshot_sha256 })}`,
      token,
      { signal },
    ),
};
