import type { MappingResult, Run, Selection } from "./types";

/** Counts here describe source rows and rules, never pretend to be instance counts. */
export function generationPlan(result: MappingResult) {
  const template = result.template;
  const tableIds = template && new Set(template.nodes.map((n) => n.table_id));
  const included = result.tables.filter((t) =>
    tableIds ? tableIds.has(t.table_id) : t.status === "mapped",
  );
  const includedIds = new Set(included.map((t) => t.table_id));
  return {
    included,
    excluded: result.tables.filter((t) => !includedIds.has(t.table_id)),
    rows: included.reduce((sum, t) => sum + t.row_count, 0),
    concepts: [
      ...new Map(
        template
          ? template.nodes.map((n) => [
              n.concept_id,
              n.concept_name || n.concept_id,
            ])
          : included.map((t) => [
              t.concept_id || t.table_id,
              t.concept_name || t.concept_id || "未匹配",
            ]),
      ).values(),
    ],
    relationRules: template
      ? template.edges.length
      : result.relations.filter((r) => r.status === "ready").length,
  };
}

export function sameSources(selections: Selection[], result: MappingResult) {
  return (
    selections.length === result.tables.length &&
    result.tables.every((t) =>
      selections.some(
        (s) => s.table_id === t.table_id && s.batch_id === t.batch_id,
      ),
    )
  );
}

export function taskStatus(run: Run) {
  if (run.graph_status === "building") return "正在生成图谱";
  if (run.graph_status === "ready") return "该版本已生成图谱";
  if (run.graph_status === "failed") return "图谱生成失败，可检查后重试";
  if (run.status === "analyzing") return run.progress;
  if (run.status === "failed") return "分析未完成";
  const failed = run.result?.tables.filter((t) => t.status === "failed").length;
  if (failed)
    return `分析结束，${failed}/${run.result!.tables.length} 张表失败，请检查原因后重试`;
  if (run.result?.template && !run.result.template.confirmed)
    return "待核对匹配并确认生成规则";
  return "规则已就绪，等待生成图谱";
}
