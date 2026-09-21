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
  profile?: string;
}
export interface GraphRagConfig {
  configured?: boolean;
  model_configured?: boolean;
  error?: string | null;
  max_upload_bytes?: number;
  profiles?: {
    id: string;
    name: string;
    description: string;
    chunk_size: number;
    overlap: number;
  }[];
  default_profile?: string;
  [key: string]: unknown;
}
export interface QueryEvidence {
  node_ids: string[];
  edge_ids: string[];
  cited_node_ids: string[];
  cited_edge_ids: string[];
}
export interface QueryResult {
  evidence?: QueryEvidence;
  trace?: unknown;
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
  evidence: Record<string, unknown>;
  conflicts: { field: string; values: { node_id: string; value: unknown }[] }[];
  status: "pending" | "merged" | "rejected" | "excluded" | "not_recommended";
  canonical_id: string | null;
}
export interface ResolutionSources {
  run_id: string;
  candidate_id: string;
  source_name: string;
  source_kind: SourceKind;
  nodes: {
    node_id: string;
    name: string;
    quote: string | null;
    quote_location: { origin: string; message: string; fields?: string[] };
    records: {
      node_id: string;
      name: string;
      description: unknown;
      source_text: string | null;
      text_unit_ids: string[];
      fields: Record<string, unknown> | null;
    }[];
  }[];
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
    excluded_count?: number;
    not_recommended_count?: number;
  };
  candidates: ResolutionCandidate[];
  audits: Audit[];
  merges: {
    candidate_id: string;
    source_nodes: Pick<KnowledgeNode, "id" | "name" | "type">[];
    target_node: Pick<KnowledgeNode, "id" | "name" | "type">;
  }[];
  diagnostics: {
    warnings?: string[];
    analysis?: {
      completed: number;
      total: number;
      failed: number;
      skipped: number;
      eta_seconds: number | null;
      preview_limit: number;
      partial: boolean;
      model_budget: number;
    };
    [key: string]: unknown;
  };
}
export interface MatchNode {
  id: string;
  name: string;
  boid: string | null;
  trace: RetrievalTrace;
  reviewed?: boolean;
}
export interface MatchEdge {
  id: string;
  source: string;
  target: string;
  edge_type: string | null;
  candidates: string[];
  proposed_edge_type?: string | null;
  reviewed?: boolean;
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
  confidence_threshold?: number;
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

export interface ResolutionOptions {
  retrieval_policy?: "balanced" | "original";
  method: "evidence_v1" | "synonym_llm_v1";
  model_policy: "review" | "apply";
  max_model_calls: number;
  max_alias_calls: number;
  concurrency: number;
  timeout_seconds: number;
  [key: string]: unknown;
}
export interface Experiment {
  name: string;
  manifest: Record<string, unknown>;
  report: {
    methods: string[];
    evaluation_status: string;
    records: number;
    changed_records: number;
    entity_counts: Record<string, number>;
    metrics: unknown;
    [key: string]: unknown;
  };
  corpus: {
    mentions: {
      mention_id: string;
      name: string;
      type: string;
      context: string;
      [key: string]: unknown;
    }[];
  };
  results: Record<
    string,
    {
      entities: {
        entity_id: string;
        mention_ids: string[];
        [key: string]: unknown;
      }[];
      memberships: {
        mention_id: string;
        entity_id: string | null;
        status: string;
      }[];
      decisions: Record<string, unknown>[];
      [key: string]: unknown;
    }
  >;
  differences: Record<string, unknown>[];
  records: Record<string, unknown>[];
  config: unknown;
  gold: unknown;
  artifacts: string[];
  aliases: unknown;
  synonyms: unknown;
}
