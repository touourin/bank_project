import type { ConceptDetail, RetrievalTrace } from "../alignment/types";

export type SourceKind = "graphrag" | "database";
export interface KnowledgeSource {
  kind: SourceKind;
  id: string;
  name: string;
  error?: string;
  root_source_id?: string;
}
export interface KnowledgeNode {
  id: string;
  name: string;
  type: string;
  properties: Record<string, unknown>;
  boid?: string | null;
}
export interface KnowledgeEdge {
  id: string;
  source: string;
  target: string;
  properties: Record<string, unknown>;
  edge_type?: string | null;
}
export interface KnowledgeGraph {
  id: string;
  name: string;
  source_kind: SourceKind;
  source_id: string;
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
}
export interface Dataset {
  key: string;
  name: string;
  status: string;
  stage: string;
  progress: number | string;
  error?: string | null;
}
export interface GraphRagConfig {
  configured?: boolean;
  model_configured?: boolean;
  error?: string | null;
  max_upload_bytes?: number;
  [key: string]: unknown;
}
export interface QueryResult {
  answer: string;
  context: unknown;
  [key: string]: unknown;
}
export interface ResolutionCandidate {
  id: string;
  node_ids: string[];
  nodes: Pick<KnowledgeNode, "id" | "name" | "type">[];
  score: number;
  reasons: string[];
  evidence: unknown;
  conflicts: { field: string; values: { node_id: string; value: unknown }[] }[];
  status: "pending" | "merged" | "rejected";
  canonical_id: string | null;
}
export interface Audit {
  id: string;
  action?: string;
  candidate_id?: string;
  canonical_id?: string;
  target?: string;
  target_id?: string;
  reviewer?: string;
  note?: string;
  created_at: string;
  revision: number;
  [key: string]: unknown;
}
export interface ResolutionRun {
  id: string;
  name: string;
  source_kind: SourceKind;
  source_id: string;
  revision: number;
  status: "analyzing" | "ready" | "failed";
  created_at: string;
  progress: string;
  error: string | null;
  summary: {
    original_node_count: number;
    original_edge_count: number;
    node_count: number;
    edge_count: number;
    pending_count: number;
    merged_count: number;
    rejected_count: number;
  };
  candidates: ResolutionCandidate[];
  audits: Audit[];
  merges: {
    candidate_id: string;
    source_nodes: Pick<KnowledgeNode, "id" | "name" | "type">[];
    target_node: Pick<KnowledgeNode, "id" | "name" | "type">;
  }[];
  diagnostics: { warnings?: string[]; [key: string]: unknown };
}
export interface MatchNode {
  id: string;
  name: string;
  boid: string | null;
  trace: RetrievalTrace;
}
export interface MatchEdge {
  id: string;
  source: string;
  target: string;
  edge_type: string | null;
  candidates: string[];
  detail: string;
  status: "matched" | "review" | "unmatched";
}
export interface MatchRun {
  id: string;
  name: string;
  source_kind: SourceKind;
  source_id: string;
  revision: number;
  status: "running" | "ready" | "failed";
  progress: string;
  error: string | null;
  created_at: string;
  ontology_revision: string;
  summary: {
    node_count: number;
    edge_count: number;
    matched_nodes: number;
    matched_edges: number;
  };
  nodes: MatchNode[];
  edges: MatchEdge[];
  audits: Audit[];
}
export type { ConceptDetail };
