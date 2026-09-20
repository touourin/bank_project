export interface Selection {
  batch_id: string;
  table_id: string;
}
export interface AlignmentConfig {
  model_configured: boolean;
  graph_configured: boolean;
  catalog_error: string | null;
  revision: string | null;
  concept_count: number;
  verification_mode: string;
  matching_mode?: string;
  retrieve_configured?: boolean;
  confidence_threshold: number;
}
export interface ColumnMapping {
  column: string;
  role: string;
  semantic: string;
  concept_id: string | null;
  concept_name: string | null;
  property_key: string;
  reason: string;
}
export interface ConceptRef {
  id: string;
  name: string;
}
export interface ConceptDetail extends ConceptRef {
  parents: ConceptRef[];
  score?: number | null;
}
export interface RetrievalTrace {
  target: "table" | "column" | "entity";
  name: string;
  query: string;
  status: "matched" | "review" | "unmatched" | "unavailable" | "mismatch";
  candidates: ConceptDetail[];
  selected: ConceptDetail | null;
  confident: boolean;
  match_method: string;
  detail: string;
}
export interface MatchTrace {
  method?: "model" | "retrieve";
  retrievals?: RetrievalTrace[];
  steps: {
    key: "meaning" | "recall" | "selection" | "validation";
    status: "pending" | "running" | "completed" | "failed" | "skipped";
    detail: string;
  }[];
  sample_rows: number[];
  meaning: null | {
    meaning: string;
    search_terms: string[];
    attribute_terms: string[];
  };
  candidates: ConceptDetail[];
  attribute_candidates: ConceptDetail[];
  selected: ConceptDetail | null;
  selection_reason: string;
  selection_confidence: number | null;
  verification: string;
  selection_attempts: number;
  confidence_threshold: number;
}
export interface MappingEdit {
  created_at: string;
  column: string | null;
  before: ConceptRef | null;
  after: ConceptDetail | null;
  reason: string;
}
export interface MappingEditRequest {
  table_id: string;
  column: string | null;
  concept_id: string | null;
  reason: string;
}
export interface TableMapping {
  table_id: string;
  batch_id: string;
  table_name: string;
  source_name?: string;
  row_count: number;
  concept_id: string | null;
  concept_name: string | null;
  confidence: number;
  status: "mapped" | "review" | "unmatched" | "failed";
  verification: string;
  reason: string;
  columns: ColumnMapping[];
  warnings: string[];
  trace?: MatchTrace | null;
  structure_notes?: string[];
  manual_edits?: MappingEdit[];
}
export interface RelationMapping {
  id: string;
  source_table_id: string;
  target_table_id: string;
  source_columns: string[];
  target_columns: string[];
  origin: "declared" | "candidate";
  reason: string;
  status: string;
  matched_rows: number;
  warnings: string[];
}
export interface MappingResult {
  template?: GraphTemplate | null;
  revision: string;
  snapshot_sha256: string;
  tables: TableMapping[];
  relations: RelationMapping[];
  warnings: string[];
}
export interface Run {
  id: string;
  created_at: string;
  status: "analyzing" | "ready" | "failed";
  progress: string;
  error: string | null;
  result: MappingResult | null;
  graph_status: "none" | "building" | "ready" | "failed";
  graph_error: string | null;
  graph_version: string | null;
  based_on_run_id?: string | null;
}
export interface GraphNodeBrief {
  id: string;
  name: string;
  table_id: string;
  table_name: string;
  source_row: number;
  concept_id: string;
  concept_name: string;
}
export interface GraphNode extends GraphNodeBrief {
  fields: Record<string, string | null>;
}
export interface GraphPreview {
  summary: null | {
    version: string;
    run_id: string;
    revision: string;
    node_count: number;
    edge_count: number;
    created_at: string;
  };
  nodes: GraphNode[];
  edges: {
    source: string;
    target: string;
    relation_id: string;
    origin: string;
    name?: string;
  }[];
}

export interface GraphGroup {
  concept_id: string;
  concept_name: string;
  count: number;
}
export interface GraphOverview {
  summary: GraphPreview["summary"];
  groups: GraphGroup[];
}
export interface GraphPage {
  nodes: GraphNodeBrief[];
  edges: GraphPreview["edges"];
  total: number;
  next_cursor: string | null;
  anchor: GraphNodeBrief | null;
  edges_truncated: boolean;
}

export interface TemplateNode {
  id: string;
  table_id: string;
  concept_id: string;
  concept_name: string;
  retrieval_target?: "table" | "entity" | null;
  retrieval_name?: string | null;
  identity_scope: string;
  key_columns: string[];
  properties: { column: string; name: string }[];
}
export interface GraphTemplate {
  mode?: "suggested" | "row_records" | "custom";
  nodes: TemplateNode[];
  edges: {
    id: string;
    source: string;
    target: string;
    name: string;
    mode: "same_row" | "join";
    source_columns: string[];
    target_columns: string[];
    reason: string;
  }[];
  confirmed: boolean;
  note: string;
}
