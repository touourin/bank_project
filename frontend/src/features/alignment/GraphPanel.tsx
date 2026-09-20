import { useState } from "react";
import { Alert, Button, Modal, Tag } from "antd";
import { DataTable } from "../../ui/DataTable";
import { Panel } from "../../ui/Panel";
import { EmptyState } from "../../ui/Feedback";
import type { GraphNode, GraphPreview } from "./types";

export function GraphPanel({
  graph,
  onRefresh,
  selectedRunId,
}: {
  graph: GraphPreview;
  onRefresh: () => void;
  selectedRunId?: string;
}) {
  const [selected, setSelected] = useState<GraphNode>();
  const positions = new Map(
    graph.nodes.map((node, i) => [
      node.id,
      { x: 70 + (i % 5) * 150, y: 40 + Math.floor(i / 5) * 75 },
    ]),
  );
  return (
    <Panel
      title="当前已发布图谱"
      actions={
        <Button size="small" onClick={onRefresh}>
          刷新图谱
        </Button>
      }
      padded
    >
      {!graph.summary ? (
        <EmptyState
          title="尚未生成图谱"
          description="完成表匹配后，点击“生成图谱”发布新版本。"
        />
      ) : (
        <>
          {selectedRunId && graph.summary.run_id !== selectedRunId && (
            <Alert
              type="info"
              showIcon
              title="此图谱来自另一条任务"
              description={`图谱来源任务 ${graph.summary.run_id.slice(0, 8)}；上方正在查看任务 ${selectedRunId.slice(0, 8)}。切换分析任务不会自动替换已发布图谱。`}
            />
          )}
          <div className="graph-metrics">
            <strong>{graph.summary.node_count.toLocaleString()} 个实例</strong>
            <strong>{graph.summary.edge_count.toLocaleString()} 条关联</strong>
            <Tag>版本 {graph.summary.version.slice(0, 8)}</Tag>
            <Tag>来源任务 {graph.summary.run_id.slice(0, 8)}</Tag>
          </div>
          {graph.summary.edge_count === 0 && (
            <p className="hint">
              这份图谱有实例节点，但没有关系，因此节点之间没有连线。点击节点可查看属性与来源。
            </p>
          )}
          <p className="hint">
            {new Date(graph.summary.created_at).toLocaleString()} · 下方预览最多
            50 个实例及其间 100 条关联，点击节点查看原始字段。虚线表示模型候选。
          </p>
          <div className="graph-canvas">
            <svg
              viewBox={`0 0 750 ${Math.max(150, Math.ceil(graph.nodes.length / 5) * 75)}`}
              role="img"
              aria-label="图谱节点与关联预览"
            >
              {graph.edges.map((edge, i) => {
                const a = positions.get(edge.source),
                  b = positions.get(edge.target);
                return a && b ? (
                  <line
                    key={i}
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    stroke="var(--subtle)"
                    strokeDasharray={
                      edge.origin === "candidate" ? "4 3" : undefined
                    }
                  />
                ) : null;
              })}
              {graph.nodes.map((node) => {
                const p = positions.get(node.id)!;
                return (
                  <g
                    key={node.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`查看 ${node.name}`}
                    onClick={() => setSelected(node)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelected(node);
                      }
                    }}
                  >
                    <circle cx={p.x} cy={p.y} r="12" fill="var(--accent)" />
                    <text
                      x={p.x}
                      y={p.y + 28}
                      textAnchor="middle"
                      fill="var(--text)"
                      fontSize="11"
                    >
                      {node.name.length > 11
                        ? node.name.slice(0, 11) + "…"
                        : node.name}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
          <DataTable
            label="图谱实例预览"
            rowKey="id"
            dataSource={graph.nodes}
            columns={[
              {
                title: "实例",
                width: 220,
                render: (_, node) => (
                  <Button type="link" onClick={() => setSelected(node)}>
                    {node.name}
                  </Button>
                ),
              },
              { title: "本体概念", dataIndex: "concept_name", width: 140 },
              { title: "来源表", dataIndex: "table_name", width: 150 },
              { title: "源行号", dataIndex: "source_row", width: 90 },
            ]}
          />
        </>
      )}
      {selected && (
        <Modal
          open
          title={selected.name}
          footer={null}
          onCancel={() => setSelected(undefined)}
        >
          <p className="hint">
            {selected.table_name} · 第 {selected.source_row} 行 ·{" "}
            {selected.concept_name}
          </p>
          <p className="hint">
            本体节点 ID：<code>{selected.concept_id}</code>
          </p>
          <dl className="node-fields">
            {Object.entries(selected.fields).map(([name, value]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>
                  {value === null ? (
                    <span className="hint">NULL</span>
                  ) : value === "" ? (
                    <span className="hint">空字符串</span>
                  ) : (
                    value
                  )}
                </dd>
              </div>
            ))}
          </dl>
        </Modal>
      )}
    </Panel>
  );
}
