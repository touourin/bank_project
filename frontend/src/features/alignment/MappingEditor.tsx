import { useCallback, useState } from "react";
import { Button, Checkbox, Input } from "antd";
import { useResource } from "../../hooks/useResource";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { DataTable } from "../../ui/DataTable";
import { ErrorNotice, LoadingState } from "../../ui/Feedback";
import { alignmentApi } from "./api";
import { ConceptLabel } from "./ConceptLabel";
import type { ConceptDetail, Run, TableMapping } from "./types";

export function MappingEditor({
  token,
  runId,
  table,
  column,
  onClose,
  onSaved,
}: {
  token: string;
  runId: string;
  table: TableMapping;
  column: string | null;
  onClose: () => void;
  onSaved: (run: Run) => void;
}) {
  const [query, setQuery] = useState<string | null>(null);
  const [selected, setSelected] = useState<ConceptDetail>();
  const [clear, setClear] = useState(false);
  const [reason, setReason] = useState("");
  const initialCandidates =
    table.trace?.method === "retrieve"
      ? table.trace.retrievals?.find((m) =>
          column === null
            ? m.target === "table"
            : m.target === "column" && m.name === column,
        )?.candidates
      : column === null
        ? table.trace?.candidates
        : table.trace?.attribute_candidates;
  const concepts = useResource(
    useCallback(
      (signal: AbortSignal) =>
        query === null && initialCandidates?.length
          ? Promise.resolve(initialCandidates)
          : alignmentApi.concepts(token, runId, query ?? "", signal),
      [token, runId, query, initialCandidates],
    ),
  );
  const current =
    column === null ? table : table.columns.find((c) => c.column === column)!;
  return (
    <ConfirmDialog
      title={column === null ? "修改表的本体节点" : `修改字段节点：${column}`}
      confirmLabel="保存为新版本"
      onClose={onClose}
      onConfirm={async () => {
        if (!selected && !clear) throw new Error("请选择一个本体节点");
        if (!reason.trim()) throw new Error("请填写修改依据");
        const updated = await alignmentApi.edit(token, runId, {
          table_id: table.table_id,
          column,
          concept_id: clear ? null : selected!.id,
          reason,
        });
        onSaved(updated);
      }}
    >
      <p>
        {table.table_name}
        {column !== null && ` / ${column}`}
      </p>
      <p className="hint">
        当前匹配：{current.concept_name || "未匹配"} {current.concept_id || ""}
      </p>
      <p className="hint">
        从本次分析使用的本体版本中选择。保存后保留原任务和已发布图谱，新版本需另行生成图谱。
      </p>
      <Input.Search
        aria-label="查找本体节点"
        placeholder="输入节点名称或完整 ID"
        enterButton="查找"
        allowClear
        onSearch={setQuery}
      />
      {concepts.error ? (
        <ErrorNotice message={concepts.error} onRetry={concepts.refresh} />
      ) : !concepts.data ? (
        <LoadingState label="读取本体节点…" />
      ) : (
        <div className="editor-candidates">
          <p className="hint">
            {query === null && initialCandidates?.length
              ? `本次${table.trace?.method === "retrieve" ? "检索" : "模型"}候选，共 ${initialCandidates.length} 个；可在上方查找本体中的其他节点。`
              : "查找结果最多 50 个；支持节点名称或完整 ID。"}
          </p>
          <DataTable
            label="可选本体节点"
            rowKey="id"
            dataSource={concepts.data}
            scroll={{ x: "max-content", y: 220 }}
            columns={[
              {
                title: "节点及直属上级",
                width: 280,
                render: (_, node) => <ConceptLabel concept={node} parents />,
              },
              {
                title: "原检索得分",
                width: 100,
                render: (_, node) => node.score?.toFixed(3) ?? "—",
              },
              {
                title: "操作",
                width: 80,
                render: (_, node) => (
                  <Button
                    size="small"
                    type={
                      selected?.id === node.id && !clear ? "primary" : "default"
                    }
                    aria-label={`选择 ${node.name} ${node.id}`}
                    onClick={() => {
                      setSelected(node);
                      setClear(false);
                    }}
                  >
                    {selected?.id === node.id && !clear ? "已选" : "选择"}
                  </Button>
                ),
              },
            ]}
          />
        </div>
      )}
      {column !== null && (
        <Checkbox checked={clear} onChange={(e) => setClear(e.target.checked)}>
          取消该字段的属性概念匹配（保留字段数据）
        </Checkbox>
      )}
      {column !== null &&
        current !== table &&
        "role" in current &&
        current.role === "ignore" && (
          <p className="hint">
            该字段原本标记为忽略；修改属性概念不会改变字段角色。
          </p>
        )}
      {selected && !clear && (
        <div className="selected-concept">
          <span className="hint">将匹配到</span>
          <ConceptLabel concept={selected} parents />
        </div>
      )}
      <label className="edit-reason">
        修改依据
        <Input.TextArea
          aria-label="修改依据"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="说明为什么选择这个节点"
          maxLength={1000}
          rows={2}
        />
      </label>
    </ConfirmDialog>
  );
}
