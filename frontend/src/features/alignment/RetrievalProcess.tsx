import { useState } from "react";
import { Input, Select, Space, Tag } from "antd";
import { DataTable } from "../../ui/DataTable";
import { PagePagination } from "../../ui/PagePagination";
import { ConceptLabel } from "./ConceptLabel";
import { ColumnHandling } from "./ColumnHandling";
import {
  RetrievalEvidence,
  matchMethods,
  matchStatus,
} from "./RetrievalEvidence";
import type { RetrievalTrace, Run, TableMapping } from "./types";

export { matchStatus } from "./RetrievalEvidence";

const targets = { table: "整表", column: "字段", entity: "实体分组" };
const titles = {
  meaning: "理解表与字段含义",
  recall: "retrieve 检索节点",
  selection: "确定匹配建议",
  validation: "校验字段与来源",
};

export function RetrievalProcess({
  table,
  runStatus,
}: {
  table: TableMapping;
  runStatus: Run["status"];
}) {
  const trace = table.trace!;
  const columns = new Map(
    table.columns.map((column) => [column.column, column]),
  );
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState(false);
  const [offset, setOffset] = useState(0);
  const matches = (trace.retrievals ?? []).filter(
    (m) =>
      (!pending || m.status !== "matched") &&
      `${m.name} ${m.query} ${m.selected?.name ?? ""}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const page = matches.slice(offset, offset + 20);
  return (
    <div
      className="matching-process"
      aria-label={`${table.table_name} 匹配过程`}
    >
      <ol className="match-stages">
        {trace.steps.map((step, index) => {
          const interrupted =
            runStatus === "failed" &&
            ["running", "pending"].includes(step.status);
          const status = interrupted ? "failed" : step.status;
          return (
            <li className={`match-stage match-stage-${status}`} key={step.key}>
              <span className="stage-number">{index + 1}</span>
              <div className="stage-body">
                <h4>{titles[step.key]}</h4>
                <p
                  className="hint"
                  role={status === "running" ? "status" : undefined}
                >
                  {interrupted
                    ? "任务中断，保留已记录结果"
                    : step.detail || "等待前序步骤完成"}
                </p>
                {step.key === "meaning" && trace.meaning && (
                  <>
                    <p>{trace.meaning.meaning}</p>
                    <p className="hint">
                      共 {table.row_count.toLocaleString()} 行 · 样例源行号{" "}
                      {trace.sample_rows.join("、")} · 仅解释字段及分组，节点由
                      retrieve 匹配。
                    </p>
                  </>
                )}
                {step.key === "selection" && trace.selected && (
                  <div className="selected-concept">
                    <ConceptLabel concept={trace.selected} parents />
                    <span>
                      原检索得分{" "}
                      {trace.selection_confidence?.toFixed(3) ?? "未提供"}
                    </span>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      <p className="hint">
        自动采用要求：版本一致、节点存在、接口标记有把握且得分 ≥{" "}
        {trace.confidence_threshold.toFixed(3)}
        。得分不是正确概率。原检索记录会保留，人工修改请到“字段映射”或图模板中操作。
      </p>
      <p className="hint">
        字段未匹配时使用“默认属性”，保留原字段名和原值，该字段无需逐项确认。
        整表和实体分组仍需要有效的本体节点。最终写入范围以生成方案为准。
      </p>
      <Space wrap>
        <Input.Search
          aria-label="筛选检索记录"
          placeholder="搜索字段、检索词或节点"
          allowClear
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOffset(0);
          }}
        />
        <Select
          aria-label="筛选匹配状态"
          value={pending ? "pending" : "all"}
          onChange={(value) => {
            setPending(value === "pending");
            setOffset(0);
          }}
          options={[
            { value: "all", label: "全部匹配" },
            { value: "pending", label: "仅待处理" },
          ]}
        />
      </Space>
      <DataTable<RetrievalTrace>
        label="retrieve 匹配记录"
        rowKey={(row) => `${row.target}:${row.name}`}
        dataSource={page}
        columns={[
          {
            title: "对象",
            width: 160,
            render: (_, m) => (
              <>
                <Tag>{targets[m.target]}</Tag>
                {m.name}
              </>
            ),
          },
          { title: "检索词", dataIndex: "query", width: 180 },
          {
            title: "接口建议节点",
            width: 260,
            render: (_, m) =>
              m.selected ? (
                <ConceptLabel concept={m.selected} parents />
              ) : (
                "未命中"
              ),
          },
          {
            title: "原始得分",
            width: 100,
            render: (_, m) => m.selected?.score?.toFixed(3) ?? "—",
          },
          {
            title: "匹配方式",
            width: 110,
            render: (_, m) => matchMethods[m.match_method] || m.match_method,
          },
          {
            title: "原检索 / 当前处理",
            width: 160,
            fixed: "right",
            render: (_, m) => {
              const column =
                m.target === "column" ? columns.get(m.name) : undefined;
              return (
                <div>
                  <Tag color={m.status === "matched" ? "green" : "orange"}>
                    {matchStatus[m.status]}
                  </Tag>
                  {column && runStatus === "ready" && (
                    <ColumnHandling column={column} />
                  )}
                </div>
              );
            },
          },
        ]}
        expandable={{
          expandedRowRender: (m) => <RetrievalEvidence trace={m} />,
        }}
      />
      <PagePagination
        offset={offset}
        pageSize={20}
        total={matches.length}
        onChange={setOffset}
        unit="项检索"
      />
    </div>
  );
}
