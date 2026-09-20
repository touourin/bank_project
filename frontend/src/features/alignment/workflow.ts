import type {
  MappingResult,
  Run,
  Selection,
  TableMapping,
  TemplateNode,
} from "./types";

export function tableLabel(table: TableMapping) {
  return table.source_name && table.source_name !== table.table_name
    ? `${table.source_name} / ${table.table_name}`
    : table.table_name;
}

export function nodeMatch(node: TemplateNode, table?: TableMapping) {
  if (node.retrieval_target && node.retrieval_name) {
    return table?.trace?.retrievals?.find(
      (m) =>
        m.target === node.retrieval_target &&
        m.name === node.retrieval_name &&
        m.selected?.id === node.concept_id,
    );
  }
  return (
    table?.trace?.retrievals?.find(
      (m) => m.target === "entity" && m.selected?.id === node.concept_id,
    ) ??
    table?.trace?.retrievals?.find(
      (m) => m.target === "table" && m.selected?.id === node.concept_id,
    )
  );
}

export function exclusionReason(table: TableMapping) {
  if (!table.row_count) return "空表，没有可生成的记录";
  if (table.status === "failed") return table.reason || "分析失败，请重试";
  if (table.verification === "mismatch")
    return "检索结果与本体版本不一致，请重新分析";
  if (table.verification === "unavailable")
    return "对象检索未完成，请重新分析或手动选择类型";
  if (!table.concept_id) return "没有有效对象候选，请重新分析或手动选择类型";
  return "当前方案未包含此表；可在高级配置中添加对象，旧任务也可重新分析获取完整建议";
}

/** Counts here describe source rows and rules, never pretend to be instance counts. */
export function generationPlan(result: MappingResult) {
  const template = result.template;
  const tableIds = template && new Set(template.nodes.map((n) => n.table_id));
  const included = result.tables.filter((t) =>
    tableIds ? tableIds.has(t.table_id) : t.status === "mapped",
  );
  const includedIds = new Set(included.map((t) => t.table_id));
  return {
    suggestedNodes:
      template?.nodes.filter((node) => {
        const table = result.tables.find((t) => t.table_id === node.table_id);
        return (
          !(
            table?.verification === "manual" &&
            node.id === table.table_id &&
            node.concept_id === table.concept_id
          ) && nodeMatch(node, table)?.status !== "matched"
        );
      }).length ?? 0,
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
    return "匹配方案已就绪，可整体采纳并生成";
  return "规则已就绪，等待生成图谱";
}
