import type { Run } from "../../src/features/alignment/types";

export function exampleRun(): Run {
  return {
    id: "template-original",
    created_at: "2026-09-19T00:00:00Z",
    status: "ready",
    progress: "分析完成",
    error: null,
    graph_status: "none",
    graph_error: null,
    graph_version: null,
    result: {
      revision: "r1",
      snapshot_sha256: "test",
      warnings: [],
      relations: [],
      tables: [
        {
          table_id: "t1",
          batch_id: "b1",
          table_name: "客户",
          row_count: 1000000,
          concept_id: "customer",
          concept_name: "客户",
          confidence: 0.9,
          status: "mapped",
          verification: "local_catalog",
          reason: "客户表",
          warnings: [],
          columns: [
            {
              column: "id",
              role: "attribute",
              semantic: "id",
              concept_id: null,
              concept_name: null,
              property_key: "field_000",
              reason: "编号",
            },
          ],
        },
      ],
      template: {
        confirmed: true,
        note: "",
        edges: [],
        nodes: [
          {
            id: "n1",
            table_id: "t1",
            concept_id: "customer",
            concept_name: "客户",
            identity_scope: "customers",
            key_columns: ["id"],
            properties: [{ column: "id", name: "编号" }],
          },
        ],
      },
    },
  };
}
