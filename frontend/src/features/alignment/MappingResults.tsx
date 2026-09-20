import { useState } from "react";
import { Button, Collapse, Input, Tabs, Tag } from "antd";
import { DataTable } from "../../ui/DataTable";
import { Panel } from "../../ui/Panel";
import { PagePagination } from "../../ui/PagePagination";
import { ConceptLabel } from "./ConceptLabel";
import { ColumnHandling } from "./ColumnHandling";
import { MatchingProcess } from "./MatchingProcess";
import { MappingEditor } from "./MappingEditor";
import { matchStatus } from "./RetrievalProcess";
import { tableLabel } from "./workflow";
import type { Run, TableMapping } from "./types";

const statuses = {
  mapped: "可生成实例",
  review: "匹配建议",
  unmatched: "未匹配",
  failed: "分析失败",
};
const verification: Record<string, string> = {
  local_catalog: "本地目录校验",
  verified: "检索一致",
  mismatch: "检索不一致",
  unavailable: "检索不可用",
  none: "未校验",
  manual: "人工确认",
};
const roles: Record<string, string> = {
  primary_key: "声明主键",
  foreign_key: "声明外键",
  attribute: "属性",
  ignore: "忽略",
};

function Fields({
  table,
  disabled,
  onEdit,
}: {
  table: TableMapping;
  disabled: boolean;
  onEdit: (column: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const columns = table.columns.filter((column) =>
    `${column.column} ${column.concept_name ?? ""} ${column.semantic}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <div className="mapping-detail">
      {table.trace?.meaning && (
        <p>
          <strong>每行数据的含义：</strong>
          {table.trace.meaning.meaning}
        </p>
      )}
      <p>原分析说明：{table.reason || "尚无分析说明"}</p>
      {table.warnings.map((warning, i) => (
        <p className="alignment-warning" key={i}>
          原分析提示：{warning}
        </p>
      ))}
      <Input.Search
        aria-label={`${table.table_name} 筛选字段`}
        placeholder="搜索字段名称或属性概念"
        allowClear
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOffset(0);
        }}
      />
      <DataTable
        label={`${table.table_name} 字段映射`}
        rowKey="column"
        dataSource={columns.slice(offset, offset + 30)}
        columns={[
          { title: "原字段", dataIndex: "column", width: 160 },
          {
            title: "角色",
            dataIndex: "role",
            render: (value) => roles[value] || value,
            width: 100,
          },
          { title: "语义", dataIndex: "semantic", width: 100 },
          {
            title: "属性概念",
            render: (_, column) => {
              const match = table.trace?.retrievals?.find(
                (m) => m.target === "column" && m.name === column.column,
              );
              const candidate =
                match?.status === "review" ? match.selected : null;
              return column.concept_id ? (
                <ConceptLabel
                  concept={{
                    id: column.concept_id,
                    name: column.concept_name || column.concept_id,
                  }}
                />
              ) : (
                <div>
                  {candidate && (
                    <>
                      <Tag color="warning">低分候选</Tag>
                      <ConceptLabel concept={candidate} />
                    </>
                  )}
                  <ColumnHandling column={column} />
                  {column.role !== "ignore" && <span>保留原字段名与原值</span>}
                </div>
              );
            },
            width: 200,
          },
          {
            title: "原检索结果",
            width: 180,
            render: (_, column) => {
              const match = table.trace?.retrievals?.find(
                (m) => m.target === "column" && m.name === column.column,
              );
              return match
                ? `${matchStatus[match.status]} · ${match.selected?.score?.toFixed(3) ?? "无分数"}`
                : "—";
            },
          },
          {
            title: "人工调整",
            width: 110,
            render: (_, column) => (
              <Button
                size="small"
                disabled={disabled}
                onClick={() => onEdit(column.column)}
                aria-label={`修改 ${column.column} 的节点`}
              >
                修改节点
              </Button>
            ),
          },
          {
            title: "说明",
            dataIndex: "reason",
            width: 300,
            render: (value) => <span className="alignment-wrap">{value}</span>,
          },
        ]}
      />
      <PagePagination
        offset={offset}
        pageSize={30}
        total={columns.length}
        onChange={setOffset}
        unit="个字段"
      />
    </div>
  );
}

export function MappingResults({
  run,
  token,
  onSaved,
  locked = false,
}: {
  run: Run;
  token: string;
  onSaved: (run: Run) => void;
  locked?: boolean;
}) {
  const result = run.result!;
  const [editing, setEditing] = useState<{
    table: TableMapping;
    column: string | null;
  }>();
  const disabled =
    locked || run.status !== "ready" || run.graph_status === "building";
  const tableName = (id: string) => {
    const table = result.tables.find((t) => t.table_id === id);
    return table ? tableLabel(table) : "未选目标表";
  };
  return (
    <Panel
      title="匹配结果"
      eyebrow="核对 1 / 2"
      description="自动展示表与字段的匹配建议，无需逐项确认。匹配有误时可修改；低分或未匹配字段保留原始属性，检索依据在“匹配过程”中查看。"
      padded
    >
      <details className="mapping-metadata">
        <summary>本体版本与分析提示</summary>
        <p className="hint ontology-revision">
          本体版本：<code>{result.revision}</code>
        </p>
        {result.warnings.map((warning, i) => (
          <p className="hint" key={i}>
            {warning}
          </p>
        ))}
      </details>
      <Collapse
        defaultActiveKey={result.tables[0] ? [result.tables[0].table_id] : []}
        items={result.tables.map((table) => ({
          key: table.table_id,
          label: (
            <div className="mapping-title">
              <strong>{tableLabel(table)}</strong>
              {table.concept_id ? (
                <ConceptLabel
                  concept={{
                    id: table.concept_id,
                    name: table.concept_name || table.concept_id,
                  }}
                />
              ) : (
                <span>尚无匹配节点</span>
              )}
              <Tag color={table.status === "mapped" ? "green" : "orange"}>
                {table.trace?.steps.some(
                  (s) => s.status === "running" || s.status === "pending",
                )
                  ? run.status === "analyzing"
                    ? table.trace.steps.some((s) => s.status === "running")
                      ? "分析中"
                      : "等待分析"
                    : "任务中断"
                  : statuses[table.status]}
              </Tag>
              <small>
                {table.trace?.method === "retrieve"
                  ? `原检索得分 ${table.trace.selection_confidence?.toFixed(3) ?? "未提供"}`
                  : table.trace && table.trace.selection_confidence === null
                    ? "尚无模型建议"
                    : `原模型 ${Math.round(table.confidence * 100)}%`}{" "}
                · {verification[table.verification]}
              </small>
            </div>
          ),
          children: (
            <>
              <div className="mapping-actions">
                <span className="hint">当前匹配用于下一次图谱生成</span>
                <Button
                  disabled={disabled || !table.row_count}
                  onClick={() => setEditing({ table, column: null })}
                >
                  修改表节点
                </Button>
              </div>
              <Tabs
                defaultActiveKey={
                  run.status === "analyzing" ? "process" : "fields"
                }
                items={[
                  {
                    key: "process",
                    label: "匹配过程",
                    children: (
                      <MatchingProcess table={table} runStatus={run.status} />
                    ),
                  },
                  {
                    key: "fields",
                    label: "字段映射",
                    children: (
                      <Fields
                        table={table}
                        disabled={disabled || !table.row_count}
                        onEdit={(column) => setEditing({ table, column })}
                      />
                    ),
                  },
                  {
                    key: "edits",
                    label: `人工修改记录（${table.manual_edits?.length || 0}）`,
                    children: table.manual_edits?.length ? (
                      <ol className="manual-edits">
                        {table.manual_edits.map((edit, index) => (
                          <li key={index}>
                            <p>
                              <Tag color="blue">
                                {edit.column === null
                                  ? "整表节点"
                                  : `字段：${edit.column}`}
                              </Tag>
                              <time>
                                {new Date(edit.created_at).toLocaleString()}
                              </time>
                            </p>
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
                            <p>修改依据：{edit.reason}</p>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p className="hint">
                        尚无人工修改，当前保留原始匹配建议。
                      </p>
                    ),
                  },
                ]}
              />
            </>
          ),
        }))}
      />
      {result.relations.length > 0 && (
        <div className="alignment-relations">
          <h3>表间关联</h3>
          <DataTable
            label="表间关联结果"
            rowKey="id"
            dataSource={result.relations}
            columns={[
              {
                title: "连接字段",
                width: 320,
                render: (_, r) => (
                  <span className="alignment-wrap">
                    {tableName(r.source_table_id)}（
                    {r.source_columns.join("、")}） →{" "}
                    {tableName(r.target_table_id)}（
                    {r.target_columns.join("、")}）
                  </span>
                ),
              },
              {
                title: "依据",
                dataIndex: "origin",
                render: (value) =>
                  value === "declared" ? "数据库声明" : "模型候选",
                width: 110,
              },
              { title: "匹配行数", dataIndex: "matched_rows", width: 100 },
              {
                title: "处理",
                dataIndex: "status",
                render: (value) => (value === "ready" ? "生成关联" : "跳过"),
                width: 80,
              },
              {
                title: "说明",
                width: 300,
                render: (_, r) => (
                  <span className="alignment-wrap">
                    {[r.reason, ...r.warnings].join("；")}
                  </span>
                ),
              },
            ]}
          />
        </div>
      )}
      <p className="hint">
        模型候选关联会保留“候选”标记；字段相等或本体路径存在，都不能单独证明真实业务关系。
      </p>
      {editing && (
        <MappingEditor
          token={token}
          runId={run.id}
          {...editing}
          onClose={() => setEditing(undefined)}
          onSaved={onSaved}
        />
      )}
    </Panel>
  );
}
