import { ReviewHistory } from "../conversion/ReviewHistory";
import { ConceptLabel } from "./ConceptLabel";
import type { TableMapping } from "./types";

export function MappingHistory({ table }: { table: TableMapping }) {
  return (
    <div className="manual-edits">
      <ReviewHistory
        entries={(table.manual_edits ?? []).map((edit, index) => ({
          id: edit.id ?? `${table.table_id}:${index}`,
          createdAt: edit.created_at,
          action:
            edit.column === null ? "修改整表节点" : `修改字段：${edit.column}`,
          reviewer: edit.reviewer,
          note: edit.reason,
          version: edit.version?.slice(0, 8) ?? "历史未记录",
          details: (
            <>
              <p className="hint">
                原匹配：
                {edit.before
                  ? `${edit.before.name}（${edit.before.id}）`
                  : "未匹配"}
              </p>
              {edit.after ? (
                <ConceptLabel concept={edit.after} parents />
              ) : (
                <p>已取消属性概念匹配，字段保留</p>
              )}
            </>
          ),
        }))}
      />
    </div>
  );
}
