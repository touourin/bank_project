export interface RiskNode {
  id: string;
  name: string;
  has_why: boolean;
  semantic_type?: string;
}

export interface RiskPredicate {
  predicate: string;
  name: string;
  description: string;
  required_params: string[];
  optional_params: string[];
  required_fields: string[];
  optional_fields: string[];
  parameter_schema: Record<string, unknown>;
}

export interface RiskCatalog {
  revision: string;
  snapshot_sha256: string;
  nodes: RiskNode[];
  predicates: RiskPredicate[];
  source?: {
    kind: "remote" | "local";
    node_count: number;
    why_node_count: number;
  };
}

export interface PropagationJob {
  id: string;
  status: "running" | "succeeded" | "failed";
  progress: string;
  error?: string | null;
  case_ids: string[];
  dataset_revision?: string;
  snapshot_sha256?: string;
  created_at?: string;
  coverage: Record<string, unknown>;
  anchors?: unknown[];
  rejections?: unknown[];
}

export interface RiskCase {
  id: string;
  name: string;
  description: string;
  version: number;
  content_hash: string;
  review_status: "pending_review" | "approved" | "rejected";
  execution_status: "blocked" | "ready";
  rule_pack: {
    head: { risk_label: string; semantics: string };
    body: { predicate: string; params: Record<string, unknown> }[];
  };
  source_binding: {
    dataset_revision: string;
    snapshot_sha256: string;
    anchor_node_id: string;
    anchor_node_name: string;
    source_node_id: string;
    source_node_name: string;
    dimension_hash: string;
    why: unknown;
    parameter_sources: Record<string, unknown>;
    propagation_path: unknown[];
    propagation: {
      bo_scope: string[];
      scope_hash: string;
      path_strength: string | number;
      truncated_by_depth?: boolean;
      truncated_by_candidates?: boolean;
      [key: string]: unknown;
    };
  };
  validation_issues: { field: string; reason: string; message?: string }[];
  audits: unknown[];
}

export interface RiskSource {
  kind: "database";
  id: string;
  name: string;
}

export interface RiskFields {
  graph_version: string;
  bo_scope: string[];
  fields: { name: string; present_count: number; samples: unknown[] }[];
  sampled_count: number;
  total_count: number;
  truncated: boolean;
}

export interface RiskExecution {
  id: string;
  status: "running" | "succeeded" | "failed";
  created_at: string;
  graph_version: string;
  start: string;
  end: string;
  field_mapping: Record<string, string>;
  error?: string | null;
  result?: {
    predicate: string;
    graph_version: string;
    start: string;
    end: string;
    bo_scope: string[];
    counts: {
      scanned: number;
      within_window: number;
      matched_transactions: number;
      hit_count: number;
    };
    hits: {
      id: string;
      account_id: string;
      day?: string;
      count: number;
      total: { value: number | string; unit: string };
      max_single?: unknown;
      transactions: {
        id: string;
        concept_id: string;
        fields: Record<string, unknown>;
      }[];
    }[];
    truncation: { scope: boolean; hits: boolean; evidence: boolean };
    limits: Record<string, number>;
  } | null;
}
