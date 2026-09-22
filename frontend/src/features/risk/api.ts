import { jsonBody, request } from "../../api/request";
import type {
  PropagationJob,
  RiskCase,
  RiskCatalog,
  RiskExecution,
  RiskFields,
  RiskSource,
} from "./types";

const root = "/api/v1/risk";
const id = encodeURIComponent;

export const riskApi = {
  catalog: (token: string, q: string, signal?: AbortSignal) =>
    request<RiskCatalog>(
      `${root}/catalog?${new URLSearchParams({ q })}`,
      token,
      { signal },
    ),
  jobs: (token: string, signal?: AbortSignal) =>
    request<PropagationJob[]>(`${root}/propagations`, token, { signal }),
  job: (token: string, key: string, signal?: AbortSignal) =>
    request<PropagationJob>(`${root}/propagations/${id(key)}`, token, {
      signal,
    }),
  propagate: (
    token: string,
    anchor_node_ids: string[],
    brief: string,
    dataset_revision: string,
  ) =>
    request<PropagationJob>(
      `${root}/propagations`,
      token,
      jsonBody({
        anchor_node_ids,
        brief,
        dataset_revision,
        max_depth: 5,
        max_candidates: 50,
      }),
    ),
  cases: (token: string, signal?: AbortSignal) =>
    request<RiskCase[]>(`${root}/cases`, token, { signal }),
  case: (token: string, key: string, signal?: AbortSignal) =>
    request<RiskCase>(`${root}/cases/${id(key)}`, token, { signal }),
  review: (
    token: string,
    value: RiskCase,
    action: "approve" | "reject",
    evidence_confirmed: boolean,
    reason: string,
  ) =>
    request<RiskCase>(
      `${root}/cases/${id(value.id)}/review`,
      token,
      jsonBody({
        expected_version: value.version,
        expected_hash: value.content_hash,
        action,
        evidence_confirmed,
        reason,
      }),
    ),
  sources: (token: string, signal?: AbortSignal) =>
    request<RiskSource[]>(`${root}/sources`, token, { signal }),
  fields: (
    token: string,
    key: string,
    graph_version: string,
    signal?: AbortSignal,
  ) =>
    request<RiskFields>(
      `${root}/cases/${id(key)}/fields?${new URLSearchParams({ graph_version })}`,
      token,
      { signal },
    ),
  executions: (token: string, key: string, signal?: AbortSignal) =>
    request<RiskExecution[]>(`${root}/cases/${id(key)}/executions`, token, {
      signal,
    }),
  execute: (
    token: string,
    value: RiskCase,
    options: {
      graph_version: string;
      field_mapping: Record<string, string>;
      start: string;
      end: string;
    },
  ) =>
    request<RiskExecution>(
      `${root}/cases/${id(value.id)}/executions`,
      token,
      jsonBody({
        expected_version: value.version,
        expected_hash: value.content_hash,
        ...options,
      }),
    ),
};
