import { useEffect, useMemo, useRef, useState } from "react";
import { Button, Input, Modal, Space, Table, Tabs } from "antd";
import cytoscape, { type Core } from "cytoscape";
import { useTheme } from "../../hooks/useTheme";
import { palettes } from "../../ui/theme";
import { EmptyState } from "../../ui/Feedback";
import { JsonDetails, pretty } from "./shared";
import type { KnowledgeGraph } from "./types";

export function KnowledgeGraphPanel({ graph }: { graph: KnowledgeGraph }) {
  const host = useRef<HTMLDivElement>(null);
  const core = useRef<Core | undefined>(undefined);
  const { appearance } = useTheme();
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState<unknown>();
  const nodes = useMemo(
    () =>
      graph.nodes.filter((node) =>
        `${node.id} ${node.name} ${node.type} ${node.boid ?? ""}`
          .toLowerCase()
          .includes(search.toLowerCase()),
      ),
    [graph, search],
  );
  const visible = useMemo(() => nodes.slice(0, 250), [nodes]);
  useEffect(() => {
    if (!host.current || !visible.length) return;
    const ids = new Set(visible.map((node) => node.id));
    const colors = palettes[appearance];
    const cy = cytoscape({
      container: host.current,
      elements: [
        ...visible.map((node) => ({
          data: {
            id: `node:${node.id}`,
            label: node.name || node.id,
            original: node,
          },
        })),
        ...graph.edges
          .filter((edge) => ids.has(edge.source) && ids.has(edge.target))
          .slice(0, 1000)
          .map((edge, index) => ({
            data: {
              id: `edge:${index}`,
              source: `node:${edge.source}`,
              target: `node:${edge.target}`,
              label: edge.edge_type || "关系",
              original: edge,
            },
          })),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": colors.accent,
            label: "data(label)",
            color: colors.text,
            "font-size": 11,
            "text-valign": "bottom",
            "text-margin-y": 7,
            "text-wrap": "ellipsis",
            "text-max-width": "140px",
            width: 30,
            height: 30,
          },
        },
        {
          selector: "edge",
          style: {
            "line-color": colors.subtle,
            "target-arrow-color": colors.subtle,
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            width: 1.3,
            opacity: 0.7,
          },
        },
        {
          selector: ":selected",
          style: { "border-width": 3, "border-color": colors.text },
        },
      ],
      layout: {
        name: "cose",
        animate: false,
        randomize: false,
        padding: 35,
        numIter: 300,
      },
      minZoom: 0.1,
      maxZoom: 4,
    });
    cy.on("tap", "node, edge", (event) =>
      setDetail(event.target.data("original")),
    );
    core.current = cy;
    const observer = new ResizeObserver(() => cy.resize());
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      cy.destroy();
      core.current = undefined;
    };
  }, [graph, visible, appearance]);
  function download() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(graph, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `${graph.name || graph.id}-graph.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <div className="knowledge-graph">
      <div className="knowledge-toolbar">
        <span>
          {graph.nodes.length.toLocaleString()} 个节点 ·{" "}
          {graph.edges.length.toLocaleString()} 条边
        </span>
        <Space>
          <Button onClick={() => core.current?.fit(undefined, 35)}>
            适应画布
          </Button>
          <Button onClick={download}>导出完整图谱</Button>
        </Space>
      </div>
      <Input.Search
        aria-label="搜索图谱节点"
        placeholder="搜索名称、节点 ID、类型或 boid"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        allowClear
      />
      {visible.length ? (
        <>
          <div
            className="knowledge-graph-canvas"
            ref={host}
            role="img"
            aria-label={`知识图谱，当前显示 ${visible.length} 个节点；节点及边详情可通过下方表格查看`}
          />
          <p className="hint">
            画布最多展示前 250 个搜索结果及 1,000
            条关系；下方表格和导出包含完整数据。点击节点或边查看原始属性。
          </p>
        </>
      ) : (
        <EmptyState title="没有符合条件的节点" />
      )}
      <Tabs
        items={[
          {
            key: "nodes",
            label: `节点（${nodes.length}）`,
            children: (
              <Table
                rowKey="id"
                size="small"
                scroll={{ x: 650 }}
                pagination={{ pageSize: 10 }}
                dataSource={nodes}
                columns={[
                  { title: "节点名称", dataIndex: "name" },
                  { title: "原始 ID", dataIndex: "id", ellipsis: true },
                  { title: "原始类型", dataIndex: "type" },
                  {
                    title: "挂载 boid",
                    dataIndex: "boid",
                    render: (value: string) => value || "未挂载",
                  },
                  {
                    title: "详情",
                    render: (_, node) => (
                      <Button size="small" onClick={() => setDetail(node)}>
                        原始属性
                      </Button>
                    ),
                  },
                ]}
              />
            ),
          },
          {
            key: "edges",
            label: `关系（${graph.edges.length}）`,
            children: (
              <Table
                rowKey="id"
                size="small"
                scroll={{ x: 650 }}
                pagination={{ pageSize: 10 }}
                dataSource={graph.edges}
                columns={[
                  { title: "起点", dataIndex: "source", ellipsis: true },
                  { title: "终点", dataIndex: "target", ellipsis: true },
                  {
                    title: "挂载边类型",
                    dataIndex: "edge_type",
                    render: (value: string) => value || "未挂载",
                  },
                  {
                    title: "详情",
                    render: (_, edge) => (
                      <Button size="small" onClick={() => setDetail(edge)}>
                        关系属性
                      </Button>
                    ),
                  },
                ]}
              />
            ),
          },
        ]}
      />
      <Modal
        title="图谱元素完整信息"
        open={detail !== undefined}
        footer={null}
        onCancel={() => setDetail(undefined)}
      >
        <pre className="knowledge-json">{pretty(detail)}</pre>
        <JsonDetails value={detail} label="展开原始 JSON" />
      </Modal>
    </div>
  );
}
