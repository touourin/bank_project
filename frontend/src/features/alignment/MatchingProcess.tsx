import { useState } from "react";
import { Collapse, Input, Tabs, Tag } from "antd";
import { DataTable } from "../../ui/DataTable";
import { ConceptLabel } from "./ConceptLabel";
import { RetrievalProcess } from "./RetrievalProcess";
import type { ConceptDetail, Run, TableMapping } from "./types";

const titles = {
  meaning: "理解表含义",
  recall: "召回本体候选",
  selection: "模型选择节点",
  validation: "校验匹配结果",
};
const states = {
  pending: "等待",
  running: "进行中",
  completed: "已完成",
  failed: "失败",
  skipped: "未执行",
};
const checks: Record<string, string> = {
  local_catalog: "本地目录校验",
  verified: "外部检索一致",
  mismatch: "外部检索不一致",
  unavailable: "外部检索不可用",
  none: "未校验",
  manual: "人工确认",
};

function Candidates({
  nodes,
  selected,
}: {
  nodes: ConceptDetail[];
  selected?: string | null;
}) {
  const [query, setQuery] = useState("");
  const filtered = nodes.filter((n) =>
    `${n.name} ${n.id}`.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <div className="candidate-list">
      <Input.Search
        aria-label="筛选本次候选节点"
        placeholder="按名称或节点 ID 筛选"
        allowClear
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <p className="hint">
        仅展示本次实际交给模型的候选，按名称召回顺序排列；顺序不代表语义置信度。
      </p>
      <DataTable
        label="本次候选节点"
        rowKey="id"
        dataSource={filtered}
        columns={[
          {
            title: "节点",
            width: 250,
            render: (_, node) => <ConceptLabel concept={node} parents />,
          },
          {
            title: "模型选择",
            width: 100,
            render: (_, node) =>
              node.id === selected ? <Tag color="blue">模型选中</Tag> : "—",
          },
        ]}
      />
    </div>
  );
}

export function MatchingProcess({
  table,
  runStatus,
}: {
  table: TableMapping;
  runStatus: Run["status"];
}) {
  const trace = table.trace;
  if (trace?.method === "retrieve")
    return <RetrievalProcess table={table} runStatus={runStatus} />;
  if (!trace)
    return (
      <p className="hint">
        这条历史任务没有保存匹配过程。重新分析后，可查看含义分析、候选节点和校验过程；原匹配结果仍可查看和人工修改。
      </p>
    );
  return (
    <div
      className="matching-process"
      aria-label={`${table.table_name} 匹配过程`}
    >
      <ol className="match-stages">
        {trace.steps.map((step, index) => {
          const interrupted =
            runStatus === "failed" &&
            (step.status === "running" || step.status === "pending");
          const status = interrupted ? "failed" : step.status;
          return (
            <li className={`match-stage match-stage-${status}`} key={step.key}>
              <span className="stage-number">{index + 1}</span>
              <div className="stage-body">
                <div className="stage-heading">
                  <h4>{titles[step.key]}</h4>
                  <Tag
                    color={
                      status === "running"
                        ? "blue"
                        : status === "failed"
                          ? "red"
                          : status === "completed"
                            ? "green"
                            : undefined
                    }
                  >
                    {interrupted ? "任务中断" : states[status]}
                  </Tag>
                </div>
                <p
                  className="hint"
                  role={status === "running" ? "status" : undefined}
                >
                  {interrupted
                    ? "任务已停止，保留停止前已记录的结果。"
                    : step.detail || "等待前序步骤完成"}
                </p>
                {step.key === "meaning" && (
                  <>
                    <p className="hint">
                      来源共 {table.row_count.toLocaleString()} 行 ·
                      样例源行号：{trace.sample_rows.join("、") || "尚未读取"} ·
                      每个样例值最多 80 字符
                    </p>
                    {trace.meaning && (
                      <>
                        <p>{trace.meaning.meaning}</p>
                        <div className="match-terms">
                          <span>概念检索词</span>
                          {trace.meaning.search_terms.map((term, i) => (
                            <Tag key={i}>{term}</Tag>
                          ))}
                        </div>
                        <div className="match-terms">
                          <span>属性检索词</span>
                          {trace.meaning.attribute_terms.map((term, i) => (
                            <Tag key={i}>{term}</Tag>
                          ))}
                        </div>
                      </>
                    )}
                  </>
                )}
                {step.key === "recall" && step.status === "completed" && (
                  <>
                    <p className="hint">
                      检索词结合原表名、字段名，在本次固定版本的本地目录中按名称召回。
                    </p>
                    <Collapse
                      items={[
                        {
                          key: "candidates",
                          label: `查看候选节点（表概念 ${trace.candidates.length} / 属性 ${trace.attribute_candidates.length}）`,
                          children: (
                            <Tabs
                              items={[
                                {
                                  key: "table",
                                  label: "表概念候选",
                                  children: (
                                    <Candidates
                                      nodes={trace.candidates}
                                      selected={trace.selected?.id}
                                    />
                                  ),
                                },
                                {
                                  key: "attributes",
                                  label: "属性候选",
                                  children: (
                                    <Candidates
                                      nodes={trace.attribute_candidates}
                                    />
                                  ),
                                },
                              ]}
                            />
                          ),
                        },
                      ]}
                    />
                  </>
                )}
                {step.key === "selection" && (
                  <>
                    {trace.selected && (
                      <div className="selected-concept">
                        <ConceptLabel concept={trace.selected} parents />
                      </div>
                    )}
                    {step.status === "completed" && (
                      <p>{trace.selection_reason}</p>
                    )}
                    {trace.selection_attempts > 1 && (
                      <p className="hint">
                        模型返回过候选范围外的 ID，执行了 1 次纠正请求。
                      </p>
                    )}
                    {step.status === "completed" && (
                      <p className="hint">
                        原模型建议置信度{" "}
                        {Math.round(
                          (trace.selection_confidence ?? table.confidence) *
                            100,
                        )}
                        %，人工修改不会改写这一数值。
                      </p>
                    )}
                  </>
                )}
                {step.key === "validation" && step.status === "completed" && (
                  <p className="hint">
                    本次阈值 {Math.round(trace.confidence_threshold * 100)}% ·{" "}
                    {checks[trace.verification]}
                    。目录校验确认节点存在，业务含义仍需核对。
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
