import { isDemoMode } from "../../demo";
import { demoKnowledgeApi } from "./demoApi";
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
  ResolutionSources,
  SourceKind,
  ResolutionOptions,
  Experiment,
} from "./types";
const root = "/api/v1";
const id = encodeURIComponent;
const liveKnowledgeApi = {
  config: (token: string, signal?: AbortSignal) =>
    request<GraphRagConfig>(`${root}/graphrag/config`, token, { signal }),
  datasets: (token: string, signal?: AbortSignal) =>
    request<Dataset[]>(`${root}/graphrag/datasets`, token, { signal }),
  upload: (
    token: string,
    file: File,
    name: string,
    options: Record<string, string> = {},
  ) =>
    request<Dataset>(
      `${root}/graphrag/uploads?${new URLSearchParams({ filename: file.name, name: name || file.name, ...options })}`,
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
  questions: (token: string, key: string, topic: string) =>
    request<QueryResult>(
      `${root}/graphrag/datasets/${id(key)}/questions`,
      token,
      jsonBody({ question: topic || "数据整体" }),
    ),
  reports: (token: string, key: string, signal?: AbortSignal) =>
    request<{ reports: Record<string, unknown>[]; communities: unknown }>(
      `${root}/graphrag/datasets/${id(key)}/reports`,
      token,
      { signal },
    ),
  experiments: (token: string, signal?: AbortSignal) =>
    request<{
      runs: { name: string; state: string; created_at: string }[];
      errors: string[];
    }>(`${root}/resolution/experiments`, token, { signal }),
  experiment: (token: string, name: string, signal?: AbortSignal) =>
    request<Experiment>(`${root}/resolution/experiments/${id(name)}`, token, {
      signal,
    }),
  sources: (token: string, signal?: AbortSignal) =>
    request<KnowledgeSource[]>(`${root}/knowledge/sources`, token, { signal }),
  sourceGraph: (
    token: string,
    kind: SourceKind,
    source: string,
    signal?: AbortSignal,
  ) =>
    request<KnowledgeGraph>(
      `${root}/knowledge/graph?${new URLSearchParams({ source_kind: kind, source_id: source })}`,
      token,
      { signal },
    ),
  resolutions: (token: string, signal?: AbortSignal) =>
    request<ResolutionRun[]>(`${root}/resolution/runs`, token, { signal }),
  resolution: (token: string, runId: string, signal?: AbortSignal) =>
    request<ResolutionRun>(`${root}/resolution/runs/${id(runId)}`, token, {
      signal,
    }),
  resolutionSources: (
    token: string,
    runId: string,
    candidateId: string,
    signal?: AbortSignal,
  ) =>
    request<ResolutionSources>(
      `${root}/resolution/runs/${id(runId)}/candidates/${id(candidateId)}/sources`,
      token,
      { signal },
    ),
  resolve: (
    token: string,
    source_kind: SourceKind,
    source_id: string,
    options?: ResolutionOptions,
  ) =>
    request<ResolutionRun>(
      `${root}/resolution/runs`,
      token,
      jsonBody({ source_kind, source_id, options }),
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
  matchAccept: (
    token: string,
    runId: string,
    decision: { expected_revision: number; reviewer?: string; note?: string },
  ) =>
    request<MatchRun>(
      `${root}/knowledge/matches/${id(runId)}/accept-proposals`,
      token,
      jsonBody(decision),
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

export const knowledgeApi: typeof liveKnowledgeApi = isDemoMode
  ? {
      ...liveKnowledgeApi,
      ...demoKnowledgeApi,
      questions: async () => ({
        answer: "哪些企业存在关联？\n这些关系有哪些原文证据？",
        context: {},
      }),
      reports: async () => ({ reports: [], communities: [] }),
      experiments: async () => ({ runs: [], errors: [] }),
    }
  : liveKnowledgeApi;

export async function streamQuery(
  token: string,
  key: string,
  question: string,
  method: string,
  onToken: (text: string) => void,
  signal: AbortSignal,
): Promise<QueryResult> {
  if (isDemoMode) {
    const result = await knowledgeApi.query(
      token,
      key,
      question,
      method,
      signal,
    );
    onToken(result.answer);
    return result;
  }
  const response = await fetch(
    `${root}/graphrag/datasets/${id(key)}/query/stream`,
    {
      ...jsonBody({ question, method }),
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      signal,
    },
  );
  if (!response.ok || !response.body)
    throw new Error(`检索请求失败（${response.status}）`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: QueryResult | undefined;
  function consume(line: string) {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.event === "error") throw new Error(event.message);
    if (event.event === "token") onToken(event.text);
    if (event.event === "result") result = event.result;
  }
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += done
        ? decoder.decode()
        : decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      lines.forEach(consume);
      if (done) {
        consume(buffer);
        break;
      }
    }
    if (!result) throw new Error("回答流意外中断，请重试");
    return result;
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}

export async function downloadExperiment(token: string, name: string) {
  const response = await fetch(
    `${root}/resolution/experiments/${id(name)}/download`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  );
  if (!response.ok) throw new Error("实验下载失败或校验未通过");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = `${name}.zip`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
