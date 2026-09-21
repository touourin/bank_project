import { Tag } from "antd";
import { DataTable } from "../../ui/DataTable";
import { ConceptLabel } from "./ConceptLabel";
import type { ConceptDetail, RetrievalTrace } from "./types";

export const matchStatus = {
  matched: "已匹配",
  review: "待确认",
  unmatched: "无候选",
  unavailable: "检索失败",
  mismatch: "版本不一致",
};

export const matchMethods: Record<string, string> = {
  exact: "精确匹配",
  fuzzy: "模糊匹配",
  vector: "向量匹配",
  none: "未命中",
};

export function RetrievalEvidence({ trace }: { trace: RetrievalTrace }) {
  return (
    <section
      className="retrieval-evidence"
      aria-label={`${trace.name} 的原检索证据`}
    >
      <h4>原检索证据</h4>
      <p className="hint">
        以下为分析时记录的检索依据；人工修改或清除挂载不会改写原检索记录。
      </p>
      <dl className="retrieval-evidence-details">
        <div>
          <dt>检索词</dt>
          <dd>{trace.query || "未记录"}</dd>
        </div>
        <div>
          <dt>原检索状态</dt>
          <dd>
            <Tag color={trace.status === "matched" ? "green" : "orange"}>
              {matchStatus[trace.status]}
            </Tag>
          </dd>
        </div>
        <div>
          <dt>匹配方式</dt>
          <dd>
            {matchMethods[trace.match_method] || trace.match_method || "未记录"}
          </dd>
        </div>
        <div>
          <dt>接口标记：</dt>
          <dd>{trace.confident ? "有把握" : "无把握"}</dd>
        </div>
        <div>
          <dt>检索说明</dt>
          <dd>{trace.detail || "未记录"}</dd>
        </div>
      </dl>
      <div className="selected-concept">
        <span className="hint">接口建议节点</span>
        {trace.selected ? (
          <>
            <ConceptLabel concept={trace.selected} parents />
            <p>原检索得分：{trace.selected.score?.toFixed(3) ?? "未提供"}</p>
          </>
        ) : (
          <span>未命中</span>
        )}
      </div>
      <DataTable<ConceptDetail>
        label={`${trace.name} 的检索候选`}
        rowKey="id"
        dataSource={trace.candidates}
        locale={{ emptyText: "本次检索未返回候选节点" }}
        columns={[
          {
            title: "候选节点",
            render: (_, node) => <ConceptLabel concept={node} parents />,
          },
          {
            title: "原检索得分",
            width: 120,
            render: (_, node) => node.score?.toFixed(3) ?? "未提供",
          },
        ]}
      />
      <p className="hint">得分是接口返回的检索相关性分数，不是正确概率。</p>
    </section>
  );
}
