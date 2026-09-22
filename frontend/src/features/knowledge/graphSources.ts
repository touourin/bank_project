import type { GraphSourceRef } from "./types";

export const derivedGraphId = (
  stage: "match" | "resolution",
  run: { id: string; revision: number },
) => `${stage}:${run.id}:${run.revision}`;
export const graphSourceKey = (source: GraphSourceRef) =>
  `${source.kind}:${source.id}`;
export const isDerivedGraph = (source: GraphSourceRef) =>
  /^(match|resolution):/.test(source.id);
export const hasDocumentIndex = (source: GraphSourceRef) =>
  source.kind === "graphrag" &&
  !isDerivedGraph(source) &&
  !source.id.startsWith("extracted:");
export const isDatabaseVersion = (source: GraphSourceRef) =>
  source.kind === "database" && !isDerivedGraph(source);
export const graphSourceLabel = (source: GraphSourceRef) =>
  `${source.kind === "database" ? "表格" : "文本"} · ${isDerivedGraph(source) ? "修订图谱" : source.id.startsWith("extracted:") ? "聚合前记录" : "原始图谱"}`;
