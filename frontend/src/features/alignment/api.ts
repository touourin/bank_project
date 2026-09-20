import { request, jsonBody } from "../../api/request";
import type {
  AlignmentConfig,
  GraphTemplate,
  ConceptDetail,
  GraphPreview,
  MappingEditRequest,
  Run,
  Selection,
} from "./types";
const base = "/api/v1/alignment";
export const alignmentApi = {
  template: (token: string, id: string, template: GraphTemplate) =>
    request<Run>(`${base}/runs/${id}/template`, token, jsonBody(template)),
  config: (token: string, signal: AbortSignal) =>
    request<AlignmentConfig>(`${base}/config`, token, { signal }),
  runs: (token: string, signal: AbortSignal) =>
    request<Run[]>(`${base}/runs`, token, { signal }),
  run: (token: string, id: string, signal: AbortSignal) =>
    request<Run>(`${base}/runs/${id}`, token, { signal }),
  analyze: (token: string, tables: Selection[]) =>
    request<Run>(`${base}/runs`, token, jsonBody({ tables })),
  generate: (token: string, id: string) =>
    request<Run>(`${base}/runs/${id}/graph`, token, { method: "POST" }),
  concepts: (token: string, id: string, query: string, signal: AbortSignal) =>
    request<ConceptDetail[]>(
      `${base}/runs/${id}/concepts?q=${encodeURIComponent(query)}`,
      token,
      { signal },
    ),
  edit: (token: string, id: string, edit: MappingEditRequest) =>
    request<Run>(`${base}/runs/${id}/mapping`, token, jsonBody(edit)),
  graph: (token: string, signal: AbortSignal) =>
    request<GraphPreview>(`${base}/graph`, token, { signal }),
};
