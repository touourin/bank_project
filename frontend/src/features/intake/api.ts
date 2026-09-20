import { request as httpRequest, jsonBody as json } from "../../api/request";
export { errorMessage } from "../../api/request";
import type {
  BatchDetail,
  IntakeJob,
  BatchPage,
  CatalogTable,
  IntakeLimits,
  MysqlConnection,
  TablePage,
} from "./types";
const base = "/api/v1/intake";

const request = <T>(path: string, token: string, init: RequestInit = {}) =>
  httpRequest<T>(base + path, token, init);
export const intakeApi = {
  jobs: (token: string, signal: AbortSignal) =>
    request<IntakeJob[]>("/jobs", token, { signal }),
  jobAction: (token: string, id: string, action: "retry" | "cancel") =>
    request<IntakeJob>(`/jobs/${id}/${action}`, token, { method: "POST" }),
  limits: (token: string, signal: AbortSignal) =>
    request<IntakeLimits>("/limits", token, { signal }),
  batches: (token: string, offset: number, signal: AbortSignal) =>
    request<BatchPage>(`/batches?offset=${offset}&limit=10`, token, { signal }),
  batch: (token: string, id: string, signal: AbortSignal) =>
    request<BatchDetail>(`/batches/${id}`, token, { signal }),
  table: (
    token: string,
    batchId: string,
    tableId: string,
    offset: number,
    signal: AbortSignal,
  ) =>
    request<TablePage>(
      `/batches/${batchId}/tables/${tableId}?offset=${offset}&limit=25`,
      token,
      { signal },
    ),
  remove: (token: string, id: string) =>
    request<void>(`/batches/${id}`, token, { method: "DELETE" }),
  upload: (
    token: string,
    file: File,
    options: {
      header_row: number;
      encoding: string;
      delimiter: string;
      skip_description_sheets: boolean;
    },
  ) =>
    request<BatchDetail | IntakeJob>(
      `/uploads?${new URLSearchParams({ filename: file.name, ...Object.fromEntries(Object.entries(options).map(([key, value]) => [key, String(value)])) })}`,
      token,
      {
        method: "POST",
        body: file,
        headers: { "Content-Type": "application/octet-stream" },
      },
    ),
  catalog: (token: string, connection: MysqlConnection | null) =>
    request<CatalogTable[]>("/mysql/catalog", token, json({ connection })),
  mysql: (
    token: string,
    connection: MysqlConnection | null,
    tables: string[],
  ) =>
    request<BatchDetail | IntakeJob>(
      "/mysql/import",
      token,
      json({ connection, tables }),
    ),
};
