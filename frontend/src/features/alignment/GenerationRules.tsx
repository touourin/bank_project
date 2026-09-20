import { useState } from "react";
import { Alert, Button, Collapse, Select, Tag } from "antd";
import { Panel } from "../../ui/Panel";
import { ErrorNotice } from "../../ui/Feedback";
import { errorMessage } from "../../api/request";
import { alignmentApi } from "./api";
import { AdvancedTemplateEditor } from "./AdvancedTemplateEditor";
import { tableLabel, nodeMatch } from "./workflow";
import type { GraphTemplate, Run } from "./types";

export function GenerationRules({
  run,
  token,
  onSaved,
  onDirty,
  onSaving,
  onDraft,
  locked,
}: {
  run: Run;
  token: string;
  onSaved: (run: Run) => void;
  onDirty: (dirty: boolean) => void;
  onSaving: (saving: boolean) => void;
  onDraft: (draft: GraphTemplate | undefined) => void;
  locked: boolean;
}) {
  const [draft, setDraft] = useState(run.result!.template!);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [edited, setEdited] = useState(false);
  const [beforeDefault, setBeforeDefault] = useState<GraphTemplate>();
  const isDefault = draft.mode === "row_records";
  const tables = run.result!.tables;
  const disabled = locked || busy || run.graph_status === "building";
  const splitTables = tables.filter(
    (t) => draft.nodes.filter((n) => n.table_id === t.table_id).length > 1,
  );
  const notes = [
    ...new Set(
      tables.flatMap((t) =>
        (t.structure_notes ?? []).map((note) => `${tableLabel(t)}：${note}`),
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
  const replace = (value: GraphTemplate) => {
    onDirty(true);
    setEdited(true);
    const next = { ...value, confirmed: false };
    setDraft(next);
    onDraft(next);
  };
  const update = (value: Partial<GraphTemplate>) =>
    replace({ ...draft, ...value, mode: "custom" });
  const nodeLabel = (id: string) => {
    const node = draft.nodes.find((n) => n.id === id);
    const table = tables.find((t) => t.table_id === node?.table_id);
    return `${node?.concept_name || node?.concept_id || id}（${table ? tableLabel(table) : "未知表"}）`;
  };

  return (
    <Panel
      title="生成方案"
      eyebrow="核对 2 / 2"
      padded
      description="已预填对象类型、身份字段和属性。可以直接整体采纳，也可以按需修改，无需逐项确认。"
      actions={
        <Tag color={draft.confirmed ? "success" : "warning"}>
          {isDefault
            ? "默认方案"
            : draft.mode === "custom"
              ? "自定义方案"
              : draft.confirmed
                ? "方案已采纳"
                : "自动建议"}
        </Tag>
      }
    >
      <div className="rule-save">
        <Button
          disabled={disabled || isDefault}
          onClick={async () => {
            setBusy(true);
            onSaving(true);
            setError("");
            try {
              const next = await alignmentApi.defaultTemplate(
                token,
                run.id,
                draft,
              );
              setBeforeDefault(draft);
              replace(next);
            } catch (reason) {
              setError(errorMessage(reason));
            } finally {
              setBusy(false);
              onSaving(false);
            }
          }}
        >
          使用默认方案
        </Button>
        {beforeDefault && (
          <Button
            disabled={disabled}
            onClick={() => {
              replace(beforeDefault);
              setBeforeDefault(undefined);
            }}
          >
            恢复之前方案
          </Button>
        )}
        <span className="hint">
          默认按一行一条记录生成，保留全部字段，不拆分、不合并、不建立关系。
        </span>
      </div>
      {isDefault && (
        <Alert
          type="info"
          showIcon
          title={
            draft.nodes.length
              ? "已使用默认方案，可直接采纳并生成"
              : "默认方案缺少对象类型，请先补充本体候选"
          }
          description="每张表使用一个对象类型；按来源行区分记录，无需选择身份字段。沿用现有本体候选，低分提示仍保留。确认下面的对象类型后即可生成，也可以继续调整。"
        />
      )}
      {isDefault && requiresReview && (
        <details className="rule-properties">
          <summary>查看原始分组建议（默认方案未采用）</summary>
          {notes.map((note) => (
            <p className="hint" key={note}>
              {note}
            </p>
          ))}
          {!notes.length && (
            <p className="hint">
              原分析包含多个对象分组；默认方案按整表保留记录。
            </p>
          )}
        </details>
      )}
      {requiresReview && !isDefault && (
        <Alert
          type={draft.confirmed ? "info" : "warning"}
          showIcon
          title="对象分组说明"
          description={
            <div>
              {notes.map((note) => (
                <p key={note}>{note}</p>
              ))}
              <p>
                {splitTables.length
                  ? `当前将 ${splitTables.map(tableLabel).join("、")} 拆成多个对象。`
                  : draft.confirmed
                    ? "当前方案按整表生成对象。"
                    : "当前方案可能仍是整表草稿。"}
                {draft.confirmed
                  ? "可修改后生成新版本。"
                  : "可整体采纳；需要调整字段归属或关系时，展开下方高级配置。"}
              </p>
            </div>
          }
        />
      )}
      {!draft.confirmed && draft.note && <p className="hint">{draft.note}</p>}
      <div className="rule-cards">
        {draft.nodes.map((node, i) => {
          const table = tables.find((t) => t.table_id === node.table_id);
          const match = nodeMatch(node, table);
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
                {match && (
                  <Tag
                    color={match.status === "matched" ? "success" : "warning"}
                  >
                    {match.status === "matched" ? "自动匹配" : "候选建议"} ·{" "}
                    {match.selected?.score?.toFixed(3) ?? "无分数"}
                  </Tag>
                )}
              </div>
              <p className="hint">
                来源：{table ? tableLabel(table) : "未知表"} ·{" "}
                {table?.row_count.toLocaleString()} 行
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
          description="没有有效本体候选，请查看未参与生成的原因；可重新分析或手动选择对象类型。"
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
                {tableLabel(table)}：{omitted.length}{" "}
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
      <div className="rule-save">
        <Button
          type="primary"
          loading={busy}
          disabled={disabled || !draft.nodes.length || !edited}
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
          仅保存方案
        </Button>
        {edited && (
          <Button
            disabled={disabled}
            onClick={() => {
              setDraft(run.result!.template!);
              onDraft(undefined);
              setEdited(false);
              onDirty(false);
              setBeforeDefault(undefined);
              setError("");
            }}
          >
            撤销未保存修改
          </Button>
        )}
        <span className="hint">
          可直接点击下方“采纳方案并生成”，会一并保存修改；不需要逐项确认。
        </span>
      </div>
      {error && <ErrorNotice message={error} />}
    </Panel>
  );
}
