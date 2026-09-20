import { useState } from "react";
import { Alert, Button, Checkbox, Collapse, Select, Tag } from "antd";
import { Panel } from "../../ui/Panel";
import { ErrorNotice } from "../../ui/Feedback";
import { errorMessage } from "../../api/request";
import { alignmentApi } from "./api";
import { AdvancedTemplateEditor } from "./AdvancedTemplateEditor";
import type { GraphTemplate, Run } from "./types";

export function GenerationRules({
  run,
  token,
  onSaved,
  onDirty,
  onSaving,
}: {
  run: Run;
  token: string;
  onSaved: (run: Run) => void;
  onDirty: (dirty: boolean) => void;
  onSaving: (saving: boolean) => void;
}) {
  const [draft, setDraft] = useState(run.result!.template!);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reviewed, setReviewed] = useState(draft.confirmed);
  const [edited, setEdited] = useState(false);
  const tables = run.result!.tables;
  const disabled = busy || run.graph_status === "building";
  const splitTables = tables.filter(
    (t) => draft.nodes.filter((n) => n.table_id === t.table_id).length > 1,
  );
  const notes = [
    ...new Set(
      tables.flatMap((t) =>
        (t.structure_notes ?? []).map((note) => `${t.table_name}：${note}`),
      ),
    ),
  ];
  // Older tasks have no structure_notes: retain their recorded grouping evidence.
  const groupingDetected = tables.some(
    (t) =>
      (t.trace?.retrievals?.filter((r) => r.target === "entity").length ?? 0) >
      1,
  );
  const requiresReview =
    notes.length > 0 || splitTables.length > 0 || groupingDetected;
  const update = (value: Partial<GraphTemplate>) => {
    onDirty(true);
    setEdited(true);
    setReviewed(false);
    setDraft({ ...draft, ...value, confirmed: false });
  };
  const nodeLabel = (id: string) => {
    const node = draft.nodes.find((n) => n.id === id);
    const table = tables.find((t) => t.table_id === node?.table_id);
    return `${node?.concept_name || node?.concept_id || id}（${table?.table_name || "未知表"}）`;
  };

  return (
    <Panel
      title="确认生成规则"
      eyebrow="核对 2 / 2"
      padded
      description="确认一行代表什么对象、用哪个编号识别，以及哪些字段写成属性。普通表核对下面的规则即可。"
      actions={
        <Tag color={draft.confirmed ? "success" : "warning"}>
          {draft.confirmed ? "规则已确认" : "待确认"}
        </Tag>
      }
    >
      {requiresReview && (
        <Alert
          type={draft.confirmed ? "info" : "warning"}
          showIcon
          title={
            draft.confirmed
              ? "多对象处理方式已确认"
              : "这张表可能包含多个对象，请先核对分组"
          }
          description={
            <div>
              {!draft.confirmed &&
                notes.map((note) => <p key={note}>{note}</p>)}
              <p>
                {splitTables.length
                  ? `当前将 ${splitTables.map((t) => t.table_name).join("、")} 拆成多个对象。`
                  : draft.confirmed
                    ? "当前已确认按整表生成对象。"
                    : "当前方案可能仍是整表草稿。"}
                {draft.confirmed
                  ? "修改后需要重新确认。"
                  : "请确认字段归属；需要拆分或补充关系时，展开下方高级配置。"}
              </p>
            </div>
          }
        />
      )}
      {!draft.confirmed && draft.note && <p className="hint">{draft.note}</p>}
      <div className="rule-cards">
        {draft.nodes.map((node, i) => {
          const table = tables.find((t) => t.table_id === node.table_id);
          const sameScope = draft.nodes.filter(
            (n) =>
              n.identity_scope === node.identity_scope &&
              n.concept_id === node.concept_id,
          );
          const shared = new Set(sameScope.map((n) => n.table_id)).size > 1;
          return (
            <article className="rule-card" key={node.id}>
              <div className="rule-card-heading">
                <span>对象 {i + 1}</span>
                <strong>{node.concept_name || node.concept_id}</strong>
              </div>
              <p className="hint">
                来源：{table?.table_name} · {table?.row_count.toLocaleString()}{" "}
                行
              </p>
              <label className="rule-identity">
                用哪些字段识别同一个对象
                <Select
                  aria-label={`对象 ${i + 1} 身份标识字段`}
                  mode="multiple"
                  disabled={disabled}
                  placeholder="不选择：每条来源记录单独生成实例"
                  value={node.key_columns}
                  options={table?.columns.map((c) => ({
                    value: c.column,
                    label: c.column,
                  }))}
                  onChange={(keys) =>
                    update({
                      nodes: draft.nodes.map((n) =>
                        n.id === node.id ? { ...n, key_columns: keys } : n,
                      ),
                    })
                  }
                />
              </label>
              <p className="hint">
                {node.key_columns.length
                  ? `按 ${node.key_columns.join(" ＋ ")} 识别；同一身份的记录合并，属性冲突会停止发布。`
                  : "没有身份字段：每条来源记录单独生成实例，不会按姓名自动合并。"}
              </p>
              {shared && (
                <Tag color="blue">已配置跨表合并，请核对编号含义一致</Tag>
              )}
              <details className="rule-properties">
                <summary>
                  写入 {node.properties.length} 个属性 · 查看字段
                </summary>
                <ul>
                  {node.properties.map((p) => (
                    <li key={p.column}>
                      {p.column}
                      {p.column !== p.name && ` → ${p.name}`}
                    </li>
                  ))}
                </ul>
              </details>
            </article>
          );
        })}
      </div>
      {!draft.nodes.length && (
        <Alert
          type="info"
          showIcon
          title="暂无可生成的对象"
          description="请先在上方匹配结果中确认表对应的本体节点。"
        />
      )}
      <div className="rule-relations">
        <strong>对象之间的关系 · {draft.edges.length} 条规则</strong>
        {draft.edges.length ? (
          <ul>
            {draft.edges.map((edge) => (
              <li key={edge.id}>
                <p>
                  {nodeLabel(edge.source)} → {edge.name} →{" "}
                  {nodeLabel(edge.target)}
                </p>
                <p className="hint">
                  {edge.mode === "same_row"
                    ? "来自同一条来源记录"
                    : `${edge.source_columns.join(" ＋ ")} = ${edge.target_columns.join(" ＋ ")}`}{" "}
                  · {edge.reason || "请补充关系依据"}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="hint">未配置关系，生成的对象之间不会建立连线。</p>
        )}
      </div>
      {tables.map((table) => {
        const nodes = draft.nodes.filter((n) => n.table_id === table.table_id);
        const used = new Set(
          nodes.flatMap((n) => n.properties.map((p) => p.column)),
        );
        const omitted = table.columns.filter((c) => !used.has(c.column));
        return (
          nodes.length > 0 &&
          omitted.length > 0 && (
            <details className="rule-properties" key={table.table_id}>
              <summary>
                {table.table_name}：{omitted.length}{" "}
                个字段不写入节点属性，原值保留在暂存库
              </summary>
              <p className="hint">{omitted.map((c) => c.column).join("、")}</p>
            </details>
          )
        );
      })}
      <Collapse
        className="advanced-rules"
        items={[
          {
            key: "advanced",
            label: "高级配置：实体拆分、跨表合并与关系",
            children: (
              <AdvancedTemplateEditor
                run={run}
                token={token}
                draft={draft}
                disabled={disabled}
                update={update}
              />
            ),
          },
        ]}
      />
      {requiresReview && !draft.confirmed && (
        <Checkbox
          className="rule-acknowledgement"
          checked={reviewed}
          disabled={disabled}
          onChange={(e) => setReviewed(e.target.checked)}
        >
          我已核对多个对象的字段归属、身份标识及关系依据
        </Checkbox>
      )}
      <div className="rule-save">
        <Button
          type="primary"
          loading={busy}
          disabled={
            disabled ||
            !draft.nodes.length ||
            draft.confirmed ||
            (requiresReview && !reviewed)
          }
          onClick={async () => {
            setBusy(true);
            onSaving(true);
            setError("");
            try {
              onSaved(await alignmentApi.template(token, run.id, draft));
            } catch (reason) {
              setError(errorMessage(reason));
            } finally {
              setBusy(false);
              onSaving(false);
            }
          }}
        >
          确认生成规则
        </Button>
        {edited && (
          <Button
            disabled={disabled}
            onClick={() => {
              setDraft(run.result!.template!);
              setReviewed(run.result!.template!.confirmed);
              setEdited(false);
              onDirty(false);
              setError("");
            }}
          >
            撤销未保存修改
          </Button>
        )}
        <span className="hint">
          确认后再点击下方“生成图谱”；修改规则会保存为新任务版本。
        </span>
      </div>
      {error && <ErrorNotice message={error} />}
    </Panel>
  );
}
