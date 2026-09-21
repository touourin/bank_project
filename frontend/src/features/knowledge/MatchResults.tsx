import { useMemo, useState } from "react";
import { Button, Input, Select, Table, Tag } from "antd";
import { ConceptLabel } from "../alignment/ConceptLabel";
import { RetrievalEvidence, matchStatus } from "../alignment/RetrievalEvidence";
import { JsonDetails } from "./shared";
import type { MatchEdge, MatchNode, MatchRun } from "./types";

function reviewedIds(run: MatchRun) {
  const nodes = new Set(
    run.nodes.filter((node) => node.reviewed).map((node) => node.id),
  );
  const edges = new Set(
    run.edges.filter((edge) => edge.reviewed).map((edge) => edge.id),
  );
  for (const audit of run.audits) {
    if (!audit.target_id || audit.action === "refresh_endpoints") continue;
    if (audit.target === "node") nodes.add(audit.target_id);
    if (audit.target === "edge") edges.add(audit.target_id);
  }
  return { nodes, edges };
}

export function matchProposalCounts(run: MatchRun) {
  const reviewed = reviewedIds(run);
  return {
    nodes: run.nodes.filter(
      (node) =>
        !node.boid &&
        !reviewed.nodes.has(node.id) &&
        node.trace.selected &&
        ["review", "matched"].includes(node.trace.status),
    ).length,
    edges: run.edges.filter(
      (edge) =>
        !edge.edge_type &&
        !reviewed.edges.has(edge.id) &&
        (edge.proposed_edge_type || edge.candidates.length === 1),
    ).length,
  };
}

export function MatchResults({
  run,
  target,
  disabled,
  onEdit,
}: {
  run: MatchRun;
  target: "node" | "edge";
  disabled: boolean;
  onEdit: (
    editing:
      | { target: "node"; value: MatchNode }
      | { target: "edge"; value: MatchEdge },
  ) => void;
}) {
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState(false);
  const [page, setPage] = useState(1);
  const nodeIndex = useMemo(
    () => new Map(run.nodes.map((node) => [node.id, node])),
    [run.nodes],
  );
  const reviewed = useMemo(() => reviewedIds(run), [run]);
  const nodeName = (id: string) => nodeIndex.get(id)?.name || id;
  const includes = (value: string) =>
    value.toLowerCase().includes(query.toLowerCase());
  const nodes =
    target === "node"
      ? run.nodes.filter(
          (node) =>
            (!pending || !node.boid) &&
            includes(
              `${node.name} ${node.id} ${node.boid ?? ""} ${node.trace.query} ${node.trace.selected?.name ?? ""} ${node.trace.selected?.id ?? ""}`,
            ),
        )
      : [];
  const edges =
    target === "edge"
      ? run.edges.filter(
          (edge) =>
            (!pending || !edge.edge_type) &&
            includes(
              `${nodeName(edge.source)} ${nodeName(edge.target)} ${edge.source} ${edge.target} ${edge.edge_type ?? ""} ${edge.candidates.join(" ")}`,
            ),
        )
      : [];
  return (
    <div className="knowledge-match-results">
      <div className="knowledge-toolbar">
        <Input.Search
          className="knowledge-match-search"
          aria-label={target === "node" ? "筛选节点匹配" : "筛选边匹配"}
          placeholder={
            target === "node"
              ? "搜索原始节点、检索词或本体概念"
              : "搜索端点或关系类型"
          }
          allowClear
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setPage(1);
          }}
        />
        <Select
          aria-label="筛选挂载状态"
          value={pending ? "pending" : "all"}
          onChange={(value) => {
            setPending(value === "pending");
            setPage(1);
          }}
          options={[
            { value: "all", label: "全部匹配" },
            { value: "pending", label: "仅未挂载" },
          ]}
        />
      </div>
      {target === "node" ? (
        <Table<MatchNode>
          rowKey="id"
          size="small"
          dataSource={nodes}
          scroll={{ x: 850 }}
          pagination={{
            pageSize: 10,
            current: page,
            onChange: setPage,
            showSizeChanger: false,
          }}
          columns={[
            {
              title: "原始节点",
              width: 160,
              render: (_, node) => (
                <span className="concept-label">
                  <strong>{node.name}</strong>
                  <code>{node.id}</code>
                </span>
              ),
            },
            {
              title: "本体匹配建议",
              width: 220,
              render: (_, node) => (
                <div>
                  {node.trace.selected ? (
                    <ConceptLabel concept={node.trace.selected} />
                  ) : (
                    "暂无建议"
                  )}
                  <div>
                    <Tag
                      color={
                        node.trace.status === "matched" ? "green" : "orange"
                      }
                    >
                      {node.trace.status === "review"
                        ? "匹配建议"
                        : matchStatus[node.trace.status]}
                    </Tag>
                  </div>
                </div>
              ),
            },
            {
              title: "原检索得分",
              width: 110,
              render: (_, node) =>
                node.trace.selected?.score?.toFixed(3) ?? "—",
            },
            {
              title: "当前挂载 BOID",
              dataIndex: "boid",
              width: 190,
              render: (value: string | null, node) => (
                <div>
                  <span>{value || "未挂载"}</span>
                  {reviewed.nodes.has(node.id) && (
                    <div>
                      <Tag color="blue">
                        {value ? "人工已确认" : "人工已清除"}
                      </Tag>
                    </div>
                  )}
                </div>
              ),
            },
            {
              title: "人工调整",
              width: 110,
              render: (_, node) => (
                <Button
                  disabled={disabled}
                  size="small"
                  onClick={() => onEdit({ target: "node", value: node })}
                >
                  修改节点
                </Button>
              ),
            },
          ]}
          expandable={{
            expandedRowRender: (node) => (
              <div className="knowledge-match-evidence">
                <RetrievalEvidence trace={node.trace} />
                <JsonDetails value={node.trace} label="完整匹配过程" />
              </div>
            ),
          }}
        />
      ) : (
        <Table<MatchEdge>
          rowKey="id"
          size="small"
          dataSource={edges}
          scroll={{ x: 820 }}
          pagination={{
            pageSize: 10,
            current: page,
            onChange: setPage,
            showSizeChanger: false,
          }}
          columns={[
            {
              title: "原始关系",
              width: 230,
              render: (_, edge) => (
                <span className="concept-label">
                  <strong>
                    {nodeName(edge.source)} → {nodeName(edge.target)}
                  </strong>
                  <code>{edge.id}</code>
                </span>
              ),
            },
            {
              title: "本体关系建议",
              width: 200,
              render: (_, edge) =>
                edge.proposed_edge_type ||
                edge.edge_type ||
                (edge.candidates.length === 1 ? edge.candidates[0] : null) ||
                (edge.candidates.length ? (
                  <>
                    <Tag color="orange">需选择类型</Tag>
                    {edge.candidates.join("、")}
                  </>
                ) : (
                  "暂无建议"
                )),
            },
            {
              title: "当前边类型",
              width: 190,
              render: (_, edge) => (
                <div>
                  {edge.edge_type || "未挂载"}
                  <div>
                    {reviewed.edges.has(edge.id) ? (
                      <Tag color="blue">
                        {edge.edge_type ? "人工已确认" : "人工已清除"}
                      </Tag>
                    ) : (
                      <Tag color={edge.edge_type ? "green" : "orange"}>
                        {edge.edge_type
                          ? "已匹配"
                          : edge.candidates.length
                            ? "匹配建议"
                            : "未匹配"}
                      </Tag>
                    )}
                  </div>
                </div>
              ),
            },
            {
              title: "人工调整",
              width: 110,
              render: (_, edge) => (
                <Button
                  size="small"
                  disabled={disabled}
                  onClick={() => onEdit({ target: "edge", value: edge })}
                >
                  修改边类型
                </Button>
              ),
            },
          ]}
          expandable={{
            expandedRowRender: (edge) => (
              <div className="knowledge-match-evidence">
                <p>
                  <strong>校验过程：</strong>
                  {edge.detail}
                </p>
                <p>
                  <strong>本体方向：</strong>
                  {nodeName(edge.source)}（
                  {nodeIndex.get(edge.source)?.boid || "未挂载"}） →{" "}
                  {nodeName(edge.target)}（
                  {nodeIndex.get(edge.target)?.boid || "未挂载"}）
                </p>
                <p>可选边类型：{edge.candidates.join("、") || "暂无候选"}</p>
                <p className="hint">
                  按两端本体节点及关系方向校验。多种候选需人工选择；原始关系描述和端点保留。
                </p>
                <JsonDetails value={edge} label="完整关系匹配信息" />
              </div>
            ),
          }}
        />
      )}
    </div>
  );
}
