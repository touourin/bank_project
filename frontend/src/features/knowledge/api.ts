import { jsonBody, request } from "../../api/request";
import type {
  ConceptDetail,
  Dataset,
  GraphRagConfig,
  KnowledgeGraph,
  KnowledgeSource,
  MatchRun,
  QueryResult,
  ResolutionRun,
  SourceKind,
} from "./types";
const root = "/api/v1";
const id = encodeURIComponent;
export const knowledgeApi = {
  config: (token: string, signal?: AbortSignal) =>
    request<GraphRagConfig>(`${root}/graphrag/config`, token, { signal }),
  datasets: (token: string, signal?: AbortSignal) =>
    request<Dataset[]>(`${root}/graphrag/datasets`, token, { signal }),
  upload: (token: string, file: File, name: string) =>
    request<Dataset>(
      `${root}/graphrag/uploads?${new URLSearchParams({ filename: file.name, name: name || file.name })}`,
      token,
      { method: "POST", headers: { "Content-Type": "text/plain" }, body: file },
    ),
  index: (token: string, key: string) =>
    request<Dataset>(
      `${root}/graphrag/datasets/${id(key)}/index`,
      token,
      jsonBody({}),
    ),
  graph: (token: string, key: string, signal?: AbortSignal) =>
    request<KnowledgeGraph>(
      `${root}/graphrag/datasets/${id(key)}/graph`,
      token,
      { signal },
    ),
  query: (
    token: string,
    key: string,
    question: string,
    method: string,
    signal?: AbortSignal,
  ) =>
    request<QueryResult>(`${root}/graphrag/datasets/${id(key)}/query`, token, {
      ...jsonBody({ question, method }),
      signal,
    }),
  sources: (token: string, signal?: AbortSignal) =>
    request<KnowledgeSource[]>(`${root}/knowledge/sources`, token, { signal }),
  resolutions: (token: string, signal?: AbortSignal) =>
    request<ResolutionRun[]>(`${root}/resolution/runs`, token, { signal }),
  resolution: (token: string, runId: string, signal?: AbortSignal) =>
    request<ResolutionRun>(`${root}/resolution/runs/${id(runId)}`, token, {
      signal,
    }),
  resolve: (token: string, source_kind: SourceKind, source_id: string) =>
    request<ResolutionRun>(
      `${root}/resolution/runs`,
      token,
      jsonBody({ source_kind, source_id }),
    ),
  resolutionDecision: (
    token: string,
    runId: string,
    decision: {
      candidate_id: string;
      action: "merge" | "reject" | "reset";
      canonical_id?: string;
      expected_revision: number;
      reviewer?: string;
      note?: string;
    },
  ) =>
    request<ResolutionRun>(
      `${root}/resolution/runs/${id(runId)}/decisions`,
      token,
      jsonBody(decision),
    ),
  manualMerge: (
    token: string,
    runId: string,
    decision: {
      node_ids: string[];
      canonical_id: string;
      expected_revision: number;
      reviewer?: string;
      note?: string;
    },
  ) =>
    request<ResolutionRun>(
      `${root}/resolution/runs/${id(runId)}/manual`,
      token,
      jsonBody(decision),
    ),
  resolutionGraph: (token: string, runId: string, signal?: AbortSignal) =>
    request<KnowledgeGraph>(
      `${root}/resolution/runs/${id(runId)}/graph`,
      token,
      { signal },
    ),
  matches: (token: string, signal?: AbortSignal) =>
    request<MatchRun[]>(`${root}/knowledge/matches`, token, { signal }),
  match: (token: string, runId: string, signal?: AbortSignal) =>
    request<MatchRun>(`${root}/knowledge/matches/${id(runId)}`, token, {
      signal,
    }),
  matchStart: (token: string, source_id: string) =>
    request<MatchRun>(
      `${root}/knowledge/matches`,
      token,
      jsonBody({ source_kind: "graphrag", source_id }),
    ),
  matchDecision: (
    token: string,
    runId: string,
    decision: {
      target: "node" | "edge";
      target_id: string;
      boid?: string | null;
      edge_type?: string | null;
      expected_revision: number;
      reviewer?: string;
      note?: string;
    },
  ) =>
    request<MatchRun>(
      `${root}/knowledge/matches/${id(runId)}/decisions`,
      token,
      jsonBody(decision),
    ),
  concepts: (token: string, runId: string, q: string, signal?: AbortSignal) =>
    request<ConceptDetail[]>(
      `${root}/knowledge/matches/${id(runId)}/concepts?${new URLSearchParams({ q })}`,
      token,
      { signal },
    ),
  matchGraph: (token: string, runId: string, signal?: AbortSignal) =>
    request<KnowledgeGraph>(
      `${root}/knowledge/matches/${id(runId)}/graph`,
      token,
      { signal },
    ),
};
