import { lazy, Suspense, useCallback, useMemo, useState } from "react";
import { Alert, Button, Input, Segmented, Select, Tag } from "antd";
import { DataTable } from "../../ui/DataTable";
import { Panel } from "../../ui/Panel";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { useResource } from "../../hooks/useResource";
import { alignmentApi } from "./api";
import { GraphNodeDetails } from "./GraphNodeDetails";
import { conceptColor, type GraphMode } from "./graphPresentation";
import type { GraphNodeBrief, GraphOverview } from "./types";

const GraphNetwork = lazy(() => import("./GraphNetwork"));

export function GraphPanel({
  graph,
  token,
  onRefresh,
  selectedRunId,
}: {
  graph: GraphOverview;
  token: string;
  onRefresh: () => void;
  selectedRunId?: string;
}) {
  return (
    <Panel
      title="当前已发布图谱"
      padded
      actions={
        <Button size="small" onClick={onRefresh}>
          刷新图谱
        </Button>
      }
    >
      {!graph.summary ? (
        <EmptyState
          title="尚未生成图谱"
          description="完成表匹配后，点击“生成图谱”发布新版本。"
        />
      ) : (
        <>
          {selectedRunId && selectedRunId !== graph.summary.run_id && (
            <Alert
              type="info"
              showIcon
              title="此图谱来自另一条任务"
              description={`图谱来源任务 ${graph.summary.run_id.slice(0, 8)}；上方正在查看任务 ${selectedRunId.slice(0, 8)}。切换分析任务不会自动替换已发布图谱。`}
            />
          )}
          <div className="graph-metrics">
            <strong>{graph.summary.node_count.toLocaleString()} 个实例</strong>
            <strong>
              {graph.summary.edge_count.toLocaleString()} 条业务关联
            </strong>
            <Tag>版本 {graph.summary.version.slice(0, 8)}</Tag>
            <Tag>来源任务 {graph.summary.run_id.slice(0, 8)}</Tag>
          </div>
          <p className="hint">
            以上为完整已发布图谱的统计。分类数量覆盖全部实例；下方按批次展开，搜索范围为此版本的全部记录。
          </p>
          <GraphExplorer
            key={graph.summary.version}
            graph={graph}
            token={token}
          />
        </>
      )}
    </Panel>
  );
}

function GraphExplorer({
  graph,
  token,
}: {
  graph: GraphOverview;
  token: string;
}) {
  const version = graph.summary!.version;
  const groups = graph.groups ?? [];
  const [mode, setMode] = useState<GraphMode>("classification");
  const [filters, setFilters] = useState({ concept: "", q: "", focus: "" });
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(100);
  const [cursors, setCursors] = useState([""]);
  const [selected, setSelected] = useState<GraphNodeBrief>();
  const after = cursors[cursors.length - 1];
  const page = useResource(
    useCallback(
      (signal: AbortSignal) =>
        alignmentApi.graphPage(
          token,
          version,
          new URLSearchParams({ ...filters, after, limit: String(limit) }),
          signal,
        ),
      [token, version, filters, after, limit],
    ),
  );
  const change = (next: typeof filters) => {
    setFilters(next);
    setCursors([""]);
  };
  const nodes = useMemo(() => {
    const data = page.data;
    if (!data) return [];
    const result = new Map(data.nodes.map((n) => [n.id, n]));
    if (data.anchor) result.set(data.anchor.id, data.anchor);
    return [...result.values()];
  }, [page.data]);
  const edges = useMemo(() => page.data?.edges ?? [], [page.data]);
  const first = (cursors.length - 1) * limit + 1;
  return (
    <div className="graph-explorer">
      <div className="graph-groups" aria-label="全部本体分类统计">
        <Button
          type={!filters.concept ? "primary" : "default"}
          onClick={() => change({ ...filters, concept: "" })}
        >
          全部类型 · {graph.summary!.node_count.toLocaleString()}
        </Button>
        {groups.map((g) => (
          <Button
            key={g.concept_id}
            type={filters.concept === g.concept_id ? "primary" : "default"}
            onClick={() => change({ ...filters, concept: g.concept_id })}
          >
            <i style={{ background: conceptColor(g.concept_id) }} />
            {g.concept_name} · {g.count.toLocaleString()}
          </Button>
        ))}
      </div>
      <div className="graph-browse-toolbar">
        <Segmented
          aria-label="图谱显示方式"
          value={mode}
          onChange={(value) => setMode(value as GraphMode)}
          options={[
            { label: "本体分类", value: "classification" },
            { label: "业务关系", value: "business" },
          ]}
        />
        <Input.Search
          aria-label="搜索全量图谱"
          placeholder="全量搜索：客户名称、编号或字段值"
          maxLength={200}
          allowClear
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            if (!e.target.value) change({ ...filters, q: "" });
          }}
          onSearch={(q) => change({ ...filters, q: q.trim() })}
          enterButton="搜索"
        />
        <Select
          aria-label="每批实例数"
          value={limit}
          options={[50, 100, 200].map((n) => ({
            value: n,
            label: `${n} 个 / 批`,
          }))}
          onChange={(n) => {
            setLimit(n);
            setCursors([""]);
          }}
        />
      </div>
      <p className="graph-view-caption">
        {mode === "classification"
          ? "大节点代表本体类型，小节点代表数据实例；点线仅表示当前分类归属，不代表交易或其他业务关系。点击分类可浏览该类全部记录。"
          : "仅显示已存储的业务关系：实线为来源或已采纳关系，虚线为候选关系。点击实例可继续查看其关联对象。"}
      </p>
      {mode === "business" && graph.summary!.edge_count === 0 && (
        <Alert
          type="info"
          showIcon
          title="当前版本没有业务关系"
          description="默认按行生成的方案保留了全部实例。需要业务连线时，请配置有来源依据的关联规则后重新生成；分类网络仍可用于查看所有数据。"
        />
      )}
      {(filters.q || filters.focus || filters.concept) && (
        <div className="graph-scope">
          <span>
            {filters.focus
              ? `当前查看：${page.data?.anchor?.name || "指定实例"} 的关联对象`
              : "当前范围"}
            {filters.q ? ` · 搜索“${filters.q}”` : ""}
            {filters.concept
              ? ` · ${groups.find((g) => g.concept_id === filters.concept)?.concept_name || filters.concept}`
              : ""}
          </span>
          <Button
            size="small"
            onClick={() => {
              change({ concept: "", q: "", focus: "" });
              setSearch("");
            }}
          >
            返回全部数据
          </Button>
        </div>
      )}
      {page.error ? (
        <ErrorNotice message={page.error} onRetry={page.refresh} />
      ) : page.loading ? (
        <LoadingState label="读取图谱实例…" />
      ) : (
        <>
          <Suspense fallback={<LoadingState label="加载网络视图…" />}>
            <GraphNetwork
              nodes={nodes}
              groups={groups}
              edges={edges}
              mode={mode}
              onNode={setSelected}
              onGroup={(concept) => change({ ...filters, concept })}
            />
          </Suspense>
          {page.data && (
            <>
              <div className="graph-page-status" role="status">
                <strong>
                  {page.data.total
                    ? `当前第 ${first.toLocaleString()}–${(first + page.data.nodes.length - 1).toLocaleString()} 条 / 共 ${page.data.total.toLocaleString()} 条`
                    : "当前范围没有匹配记录"}
                </strong>
                <span>
                  画布仅展开本批实例{page.data.anchor ? "及中心实例" : ""}
                  ，切换批次可查看其余记录。
                </span>
              </div>
              {mode === "business" &&
                graph.summary!.edge_count > 0 &&
                !edges.length && (
                  <p className="hint">
                    本批实例之间没有连线；可点击实例，再选择“查看此实例的业务关联”。
                  </p>
                )}
              {page.data.edges_truncated && (
                <Alert
                  type="warning"
                  showIcon
                  title="本批关系超过 500 条，画布仅显示前 500 条"
                  description="请查看单个实例的关联对象或缩小每批数量。实例分页仍覆盖全部匹配记录。"
                />
              )}
              <DataTable
                label="图谱实例预览"
                rowKey="id"
                dataSource={page.data.nodes}
                columns={[
                  {
                    title: "实例",
                    width: 260,
                    render: (_, n) => (
                      <Button
                        type="link"
                        aria-label={`查看 ${n.name}`}
                        onClick={() => setSelected(n)}
                      >
                        {n.name}
                      </Button>
                    ),
                  },
                  { title: "本体概念", dataIndex: "concept_name", width: 150 },
                  { title: "来源表", dataIndex: "table_name", width: 130 },
                  { title: "源行号", dataIndex: "source_row", width: 90 },
                ]}
              />
              <div className="graph-pagination">
                <Button
                  disabled={cursors.length === 1}
                  onClick={() => setCursors(cursors.slice(0, -1))}
                >
                  上一批
                </Button>
                <span>
                  第 {cursors.length} /{" "}
                  {Math.max(1, Math.ceil(page.data.total / limit))} 批
                </span>
                <Button
                  disabled={!page.data.next_cursor}
                  onClick={() =>
                    setCursors([...cursors, page.data!.next_cursor!])
                  }
                >
                  下一批
                </Button>
              </div>
            </>
          )}
        </>
      )}
      {selected && (
        <GraphNodeDetails
          key={selected.id}
          token={token}
          version={version}
          node={selected}
          onClose={() => setSelected(undefined)}
          onNeighbors={() => {
            change({ concept: "", q: "", focus: selected.id });
            setSearch("");
            setMode("business");
            setSelected(undefined);
          }}
        />
      )}
    </div>
  );
}
