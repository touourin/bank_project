import { useEffect, useRef, useState } from "react";
import cytoscape, { type Core } from "cytoscape";
import { Button, Checkbox, Tooltip } from "antd";
import {
  Expand,
  Minimize,
  Maximize,
  ZoomIn,
  ZoomOut,
  Download,
} from "lucide-react";
import { useTheme } from "../../hooks/useTheme";
import { palettes } from "../../ui/theme";
import { graphElements, type GraphMode } from "./graphPresentation";
import type { GraphGroup, GraphNodeBrief, GraphPage } from "./types";

export default function GraphNetwork({
  nodes,
  groups,
  edges,
  mode,
  onNode,
  onGroup,
}: {
  nodes: GraphNodeBrief[];
  groups: GraphGroup[];
  edges: GraphPage["edges"];
  mode: GraphMode;
  onNode: (node: GraphNodeBrief) => void;
  onGroup: (id: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const core = useRef<Core | undefined>(undefined);
  const callbacks = useRef({ onNode, onGroup });
  callbacks.current = { onNode, onGroup };
  const [expanded, setExpanded] = useState(false);
  const [labels, setLabels] = useState(false);
  const [hover, setHover] = useState("");
  const { appearance } = useTheme();
  useEffect(() => {
    if (!host.current) return;
    const colors = palettes[appearance];
    const cy = cytoscape({
      container: host.current,
      elements: graphElements(nodes, groups, edges, mode),
      minZoom: 0.1,
      maxZoom: 4,
      boxSelectionEnabled: false,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            width: "data(size)",
            height: "data(size)",
            label: "",
            color: colors.text,
            "font-size": 11,
            "text-wrap": "wrap",
            "text-max-width": "150px",
            "text-valign": "bottom",
            "text-margin-y": 8,
            "text-background-color": colors.canvas,
            "text-background-opacity": 0.85,
            "text-background-padding": "3px",
            "border-width": 1,
            "border-color": colors.surface,
          },
        },
        {
          selector: 'node[kind="type"]',
          style: {
            label: "data(label)",
            "font-size": 11,
            "text-valign": "center",
            "text-margin-y": 0,
            "text-max-width": "85px",
            "text-background-opacity": 0,
            color: "#142c21",
            "font-weight": 600,
            "border-width": 5,
            "border-opacity": 0.3,
            "border-color": "data(color)",
          },
        },
        {
          selector: "edge",
          style: {
            width: 0.8,
            "line-color": colors.subtle,
            opacity: 0.5,
            "curve-style": "bezier",
            "target-arrow-color": colors.subtle,
            "arrow-scale": 0.65,
            "font-size": 10,
            color: colors.text,
          },
        },
        {
          selector: 'edge[kind="membership"]',
          style: { "line-style": "dotted", opacity: 0.32 },
        },
        {
          selector: 'edge[kind="business"], edge[kind="candidate"]',
          style: { "target-arrow-shape": "triangle" },
        },
        {
          selector: 'edge[kind="candidate"]',
          style: { "line-style": "dashed" },
        },
        {
          selector: "node.show-labels, node.highlight",
          style: { label: "data(label)", "z-index": 10 },
        },
        {
          selector: "edge.highlight",
          style: {
            label: "data(label)",
            width: 2,
            opacity: 1,
            "text-background-color": colors.canvas,
            "text-background-opacity": 1,
            "text-background-padding": "3px",
          },
        },
        {
          selector: "node.highlight",
          style: { "border-width": 3, "border-color": colors.text },
        },
      ],
      layout: {
        name: "cose",
        animate: false,
        randomize: false,
        fit: true,
        padding: 60,
        nodeRepulsion: () => 12000,
        idealEdgeLength: () => (mode === "classification" ? 110 : 85),
        gravity: 0.15,
        componentSpacing: 110,
        numIter: 600,
      },
    });
    core.current = cy;
    const byId = new Map(nodes.map((n) => [n.id, n]));
    cy.on("tap", "node", (event) => {
      const data = event.target.data();
      if (data.kind === "type") callbacks.current.onGroup(data.concept);
      else {
        const node = byId.get(data.instance);
        if (node) callbacks.current.onNode(node);
      }
    });
    cy.on("mouseover", "node, edge", (event) => {
      event.target.addClass("highlight");
      setHover(event.target.data("label"));
    });
    cy.on("mouseout", "node, edge", (event) => {
      event.target.removeClass("highlight");
      setHover("");
    });
    const observer = new ResizeObserver(() => {
      cy.resize();
      cy.fit(undefined, 60);
    });
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      cy.destroy();
      core.current = undefined;
    };
  }, [nodes, groups, edges, mode, appearance]);
  useEffect(() => {
    core.current?.nodes().toggleClass("show-labels", labels);
  }, [labels, nodes, groups, edges, mode, appearance]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, []);
  const zoom = (factor: number) => {
    const cy = core.current;
    if (cy)
      cy.zoom({
        level: Math.max(
          cy.minZoom(),
          Math.min(cy.maxZoom(), cy.zoom() * factor),
        ),
        renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
      });
  };
  return (
    <div
      className={`graph-network ${expanded ? "graph-network-expanded" : ""}`}
    >
      <div className="graph-network-heading">
        <strong>
          {mode === "classification" ? "本体分类网络" : "业务关系网络"}
        </strong>
        <span>{nodes.length} 个已展开实例 · 拖动节点或画布，滚轮缩放</span>
      </div>
      <div
        ref={host}
        className="graph-network-host"
        role="img"
        aria-label={`${mode === "classification" ? "本体分类" : "业务关系"}网络，${nodes.length} 个实例。可通过下方表格查看详情。`}
      />
      {hover && (
        <div className="graph-network-tooltip" role="status">
          {hover}
        </div>
      )}
      <div className="graph-network-tools">
        <Checkbox
          checked={labels}
          onChange={(e) => setLabels(e.target.checked)}
        >
          显示名称
        </Checkbox>
        <Tooltip title="放大">
          <Button
            aria-label="放大图谱"
            icon={<ZoomIn size={17} />}
            onClick={() => zoom(1.3)}
          />
        </Tooltip>
        <Tooltip title="缩小">
          <Button
            aria-label="缩小图谱"
            icon={<ZoomOut size={17} />}
            onClick={() => zoom(1 / 1.3)}
          />
        </Tooltip>
        <Tooltip title="适应画布">
          <Button
            aria-label="适应画布"
            icon={<Maximize size={17} />}
            onClick={() => core.current?.fit(undefined, 60)}
          />
        </Tooltip>
        <Tooltip title={expanded ? "收起图谱" : "展开图谱"}>
          <Button
            aria-label={expanded ? "收起图谱" : "展开图谱"}
            icon={expanded ? <Minimize size={17} /> : <Expand size={17} />}
            onClick={() => setExpanded(!expanded)}
          />
        </Tooltip>
        <Tooltip title="导出当前画面">
          <Button
            aria-label="导出图谱图片"
            icon={<Download size={17} />}
            onClick={() => {
              const cy = core.current;
              if (!cy) return;
              const a = document.createElement("a");
              a.href = cy.png({
                bg: palettes[appearance].canvas,
                scale: 2,
                full: false,
              });
              a.download =
                mode === "classification"
                  ? "本体分类网络-当前批次.png"
                  : "业务关系网络-当前批次.png";
              a.click();
            }}
          />
        </Tooltip>
      </div>
    </div>
  );
}
