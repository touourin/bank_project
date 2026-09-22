import { useCallback, useEffect } from "react";
import { Alert, Button, Tabs } from "antd";
import { isDemoMode } from "../../demo";
import { useResource } from "../../hooks/useResource";
import { ErrorNotice, LoadingState } from "../../ui/Feedback";
import { Panel } from "../../ui/Panel";
import { GraphPanel } from "../alignment/GraphPanel";
import { alignmentApi } from "../alignment/api";
import { knowledgeApi } from "../knowledge/api";
import { GraphChat } from "../knowledge/GraphChat";
import { KnowledgeGraphPanel } from "../knowledge/KnowledgeGraphPanel";
import { ReportsPanel } from "../knowledge/ReportsPanel";
import { hasDocumentIndex, isDatabaseVersion } from "../knowledge/graphSources";
import type { GraphSourceRef, KnowledgeSource } from "../knowledge/types";

type Props = {
  token: string;
  source: KnowledgeSource;
  active: boolean;
  onSelect: (source: GraphSourceRef) => void;
};

export function GraphResults(props: Props) {
  return isDatabaseVersion(props.source) && !isDemoMode ? (
    <DatabaseResults {...props} />
  ) : (
    <KnowledgeResults {...props} />
  );
}

function DatabaseResults({ token, source, active }: Props) {
  const graph = useResource(
    useCallback(
      (signal: AbortSignal) =>
        alignmentApi.graphVersion(token, source.id, signal),
      [token, source.id],
    ),
    true,
  );
  const { refresh } = graph;
  useEffect(() => {
    if (active) refresh();
  }, [active, refresh]);
  return (
    <>
      <Alert
        type="info"
        showIcon
        title="表格图谱支持全量搜索、分页浏览和来源字段查看。文档问答需选择已建立索引的文本图谱。"
      />
      {graph.error ? (
        <ErrorNotice message={graph.error} onRetry={refresh} />
      ) : graph.data ? (
        <GraphPanel
          token={token}
          graph={graph.data}
          onRefresh={refresh}
          title="表格图谱"
        />
      ) : (
        <LoadingState label="读取表格图谱…" />
      )}
    </>
  );
}

function KnowledgeResults({ token, source, active, onSelect }: Props) {
  const indexed = hasDocumentIndex(source);
  const graph = useResource(
    useCallback(
      (signal: AbortSignal) =>
        indexed
          ? knowledgeApi.graph(token, source.id, signal)
          : knowledgeApi.sourceGraph(token, source.kind, source.id, signal),
      [token, source.kind, source.id, indexed],
    ),
    true,
  );
  const { refresh } = graph;
  useEffect(() => {
    if (active) refresh();
  }, [active, refresh]);
  if (graph.error)
    return <ErrorNotice message={graph.error} onRetry={refresh} />;
  if (!graph.data) return <LoadingState label="读取图谱版本…" />;
  return (
    <Panel
      title={source.name}
      padded
      actions={
        <Button size="small" onClick={refresh}>
          刷新图谱
        </Button>
      }
    >
      {!indexed && (
        <Alert
          type="info"
          showIcon
          title="当前版本提供图谱浏览与导出，尚未建立独立的问答索引。"
          description="匹配和消歧不会自动更新文档原有的社区报告与问答依据。"
          action={
            source.kind === "graphrag" &&
            source.root_source_id &&
            source.root_source_id !== source.id ? (
              <Button
                onClick={() =>
                  onSelect({ kind: "graphrag", id: source.root_source_id! })
                }
              >
                查看原始索引
              </Button>
            ) : undefined
          }
        />
      )}
      <Tabs
        items={[
          {
            key: "graph",
            label: "图谱浏览",
            children: <KnowledgeGraphPanel graph={graph.data} />,
          },
          ...(indexed
            ? [
                {
                  key: "query",
                  label: "图谱问答",
                  children: (
                    <GraphChat
                      token={token}
                      datasetKey={source.id}
                      graph={graph.data}
                    />
                  ),
                },
                {
                  key: "reports",
                  label: "社区报告",
                  children: (
                    <ReportsPanel token={token} datasetKey={source.id} />
                  ),
                },
              ]
            : []),
        ]}
      />
    </Panel>
  );
}
