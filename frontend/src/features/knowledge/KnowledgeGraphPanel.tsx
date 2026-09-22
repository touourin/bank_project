import { useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Input,
  Modal,
  Select,
  InputNumber,
  Space,
  Table,
  Tabs,
} from "antd";
import cytoscape, { type Core } from "cytoscape";
import { useTheme } from "../../hooks/useTheme";
import { palettes } from "../../ui/theme";
import { EmptyState } from "../../ui/Feedback";
import { JsonDetails, pretty } from "./shared";
import { selectGraph } from "./graphViews";
import type { GraphData, QueryEvidence, KnowledgeNode } from "./types";

const enterpriseColors: Record<string, string> = {
  组织机构: "#4c6ef5",
  人物: "#f76707",
  产品或技术: "#2f9e44",
  事件: "#ae3ec9",
  项目或合同: "#0ca678",
  财务指标: "#e67700",
  战略关键词: "#e64980",
  地点: "#8d6e63",
};

export function KnowledgeGraphPanel({
  graph,
  evidence,
  nodeLabel = "实体",
}: {
  graph: GraphData;
  nodeLabel?: "实体" | "概念";
  evidence?: QueryEvidence;
}) {
  const [mode, setMode] = useState("core");
  const [center, setCenter] = useState(graph.nodes[0]?.id ?? "");
  const [depth, setDepth] = useState(1);
  const [limit, setLimit] = useState(50);
  const [types, setTypes] = useState<string[]>([]);
  useEffect(() => {
    if (evidence) setMode("answer");
  }, [evidence]);
  useEffect(() => {
    setCenter(graph.nodes[0]?.id ?? "");
    setTypes([]);
  }, [graph.id]);

  const host = useRef<HTMLDivElement>(null);
  const core = useRef<Core | undefined>(undefined);
  const { appearance } = useTheme();
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState<unknown>();
  const typeColors = useMemo(() => {
    const colors = palettes[appearance];
    const options = [
      colors.accent,
      colors.blue,
      colors.amber,
      colors.red,
      appearance === "dark" ? "#bba4e4" : "#8061b2",
      appearance === "dark" ? "#79c6ca" : "#33848b",
    ];
    return new Map(
      [...new Set(graph.nodes.map((node) => node.type || "未分类"))]
        .sort()
        .map((type, index) => [
          type,
          enterpriseColors[type] ?? options[index % options.length],
        ]),
    );
  }, [graph, appearance]);
  const nodes = useMemo(
    () =>
      graph.nodes.filter((node) =>
        `${node.id} ${node.name} ${node.type} ${node.boid ?? ""}`
          .toLowerCase()
          .includes(search.toLowerCase()),
      ),
    [graph, search],
  );
  const view = useMemo(
    () =>
      selectGraph(
        { ...graph, nodes },
        mode,
        center,
        depth,
        limit,
        types,
        evidence,
      ),
    [graph, nodes, mode, center, depth, limit, types, evidence],
  );
  const visible = view.nodes;
  useEffect(() => {
    if (!host.current || !visible.length) return;
    const ids = new Set(visible.map((node) => node.id));
    const colors = palettes[appearance];
    const cy = cytoscape({
      container: host.current,
      elements: [
        ...visible.map((node, index) => ({
          data: {
            id: `node:${node.id}`,
            label: node.name || node.id,
            color: typeColors.get(node.type || "未分类"),
            original: node,
            size:
              24 +
              Math.min(
                35,
                8 *
                  Math.log1p(
                    Number(
                      node.properties.degree ?? view.degrees.get(node.id) ?? 0,
                    ),
                  ),
              ),
            cited: evidence?.cited_node_ids.includes(node.id) ? "yes" : "no",
          },
          position: {
            x: Math.cos((index / visible.length) * Math.PI * 2) * 240,
            y: Math.sin((index / visible.length) * Math.PI * 2) * 240,
          },
        })),
        ...view.edges
          .filter((edge) => ids.has(edge.source) && ids.has(edge.target))
          .slice(0, 1000)
          .map((edge, index) => ({
            data: {
              id: `edge:${index}`,
              source: `node:${edge.source}`,
              target: `node:${edge.target}`,
              label: edge.edge_type || "关系",
              original: edge,
              cited: evidence?.cited_edge_ids.includes(edge.id) ? "yes" : "no",
            },
          })),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            label: "data(label)",
            color: colors.text,
            "font-size": 11,
            "text-valign": "bottom",
            "text-margin-y": 7,
            "text-wrap": "ellipsis",
            "text-max-width": "140px",
            "text-background-color": colors.surface,
            "text-background-opacity": 0.9,
            "text-background-padding": "3px",
            "border-width": 2,
            "border-color": colors.surface,
            width: "data(size)",
            height: "data(size)",
          },
        },
        {
          selector: "edge",
          style: {
            "line-color": colors.subtle,
            "target-arrow-color": colors.subtle,
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "data(label)",
            color: colors.muted,
            "font-size": 10,
            "text-rotation": "autorotate",
            "text-background-color": colors.surface,
            "text-background-opacity": 0.95,
            "text-background-padding": "3px",
            width: 1.3,
            opacity: 0.9,
          },
        },
        {
          selector: ":selected",
          style: { "border-width": 3, "border-color": colors.text },
        },
        {
          selector: 'node[cited="yes"]',
          style: { "border-color": "#e03131", "border-width": 4 },
        },
        {
          selector: 'edge[cited="yes"]',
          style: {
            "line-color": "#e03131",
            "target-arrow-color": "#e03131",
            width: 3,
          },
        },
      ],
      layout:
        visible.length <= 20
          ? {
              name: "circle",
              animate: false,
              padding: 55,
              radius: 155,
              avoidOverlap: false,
            }
          : {
              name: "cose",
              animate: false,
              randomize: false,
              padding: 50,
              nodeRepulsion: 6000,
              idealEdgeLength: 110,
              componentSpacing: 90,
              numIter: 500,
            },
      minZoom: 0.1,
      maxZoom: 4,
    });
    cy.on("tap", "node, edge", (event) =>
      setDetail(event.target.data("original")),
    );
    core.current = cy;
    let frame = 0;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry.contentRect.width || !entry.contentRect.height) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        cy.resize();
        cy.fit(undefined, 50);
      });
    });
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
      cy.destroy();
      core.current = undefined;
    };
  }, [graph, visible, view, evidence, appearance, typeColors]);
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
      <div className="graph-controls">
        <label>
          图谱视图
          <Select
            aria-label="图谱视图"
            value={mode}
            onChange={setMode}
            options={[
              { value: "core", label: "核心网络" },
              { value: "ego", label: `以中心${nodeLabel}展开` },
              { value: "answer", label: "答案依据", disabled: !evidence },
            ]}
          />
        </label>
        <label>
          中心{nodeLabel}
          <Select
            aria-label={`中心${nodeLabel}`}
            showSearch
            optionFilterProp="label"
            value={center}
            onChange={(value) => {
              setCenter(value);
              setMode("ego");
            }}
            options={graph.nodes.map((n) => ({ value: n.id, label: n.name }))}
          />
        </label>
        <label>
          展开深度
          <InputNumber
            aria-label="展开深度"
            min={1}
            max={3}
            value={depth}
            onChange={(v) => setDepth(v ?? 1)}
          />
        </label>
        <label>
          节点上限
          <InputNumber
            aria-label="节点上限"
            min={5}
            max={250}
            value={limit}
            onChange={(v) => setLimit(v ?? 50)}
          />
        </label>
        <label>
          {nodeLabel}类型
          <Select
            aria-label={`${nodeLabel}类型筛选`}
            mode="multiple"
            value={types}
            onChange={setTypes}
            placeholder="所有类型"
            options={[...typeColors.keys()].map((t) => ({
              value: t,
              label: t,
            }))}
          />
        </label>
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
            aria-label="节点类型图例"
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "8px 18px",
              padding: "12px 0",
              fontSize: 12,
            }}
          >
            {[...typeColors].map(([type, color]) => (
              <span
                key={type}
                style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
              >
                <span
                  aria-hidden="true"
                  style={{
                    width: 9,
                    height: 9,
                    borderRadius: "50%",
                    background: color,
                  }}
                />
                {type}
              </span>
            ))}
          </div>
          <div
            className="knowledge-graph-canvas"
            ref={host}
            role="img"
            aria-label={`知识图谱，当前显示 ${visible.length} 个节点；节点及边详情可通过下方表格查看`}
          />
          <p className="hint">
            画布按连接数量、关系权重和展开深度选取子图，最多显示 400
            条关系。下方表格和导出包含完整数据。
            {evidence && "红色标记为回答直接引用。"}
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
        {!!detail && typeof detail === "object" && "name" in detail && (
          <Button
            onClick={() => {
              setCenter((detail as KnowledgeNode).id);
              setMode("ego");
              setSearch("");
              setDetail(undefined);
            }}
          >
            以此节点为中心
          </Button>
        )}
        <pre className="knowledge-json">{pretty(detail)}</pre>
        <JsonDetails value={detail} label="展开原始 JSON" />
      </Modal>
    </div>
  );
}
