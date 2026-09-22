import { useCallback } from "react";
import { Button, Tabs } from "antd";
import { useResource } from "../../hooks/useResource";
import { Panel } from "../../ui/Panel";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { knowledgeApi } from "./api";
import { KnowledgeGraphPanel } from "./KnowledgeGraphPanel";
import { MatchingPanel } from "./MatchingPanel";
import { TextDatasetPicker } from "./TextDatasetPicker";
import { TextIndexPanel } from "./TextIndexPanel";
import {
  isDatasetReady,
  isDatasetRunning,
  useTextDatasets,
} from "./useTextDatasets";
import type { GraphSourceRef } from "./types";
import "./knowledge.css";

export function TextConversionWorkspace({
  token,
  active,
  onAnalyze,
}: {
  token: string;
  active: boolean;
  onAnalyze: (source: GraphSourceRef) => void;
}) {
  const datasets = useTextDatasets(token, active);
  const { dataset } = datasets;
  const ready = isDatasetReady(dataset);
  const key = dataset?.key;
  const graph = useResource(
    useCallback(
      (signal: AbortSignal) =>
        key && ready
          ? knowledgeApi.graph(token, key, signal)
          : Promise.resolve(null),
      [token, key, ready],
    ),
  );
  return (
    <div className="knowledge-layout">
      <aside className="knowledge-sidebar">
        <TextDatasetPicker
          datasets={datasets.data}
          selected={key}
          loading={datasets.loading}
          error={datasets.error}
          onSelect={datasets.select}
          onRefresh={datasets.refresh}
        />
        <TextIndexPanel
          key={key ?? "empty"}
          token={token}
          dataset={dataset}
          onRefresh={datasets.refresh}
        />
      </aside>
      <div className="knowledge-results">
        {ready && key ? (
          <Panel
            title={dataset?.name}
            description="核对抽取结果与本体匹配；问答和社区报告可在图谱分析中查看。"
            actions={
              <Button onClick={() => onAnalyze({ kind: "graphrag", id: key })}>
                前往图谱分析
              </Button>
            }
            padded
          >
            <Tabs
              key={key}
              items={[
                {
                  key: "graph",
                  label: "转换结果",
                  children: graph.error ? (
                    <ErrorNotice
                      message={graph.error}
                      onRetry={graph.refresh}
                    />
                  ) : graph.data ? (
                    <KnowledgeGraphPanel graph={graph.data} />
                  ) : (
                    <LoadingState label="读取文本图谱…" />
                  ),
                },
                {
                  key: "matching",
                  label: "节点与边匹配",
                  children: (
                    <MatchingPanel
                      key={`${token}:${key}`}
                      token={token}
                      sourceId={key}
                    />
                  ),
                },
              ]}
            />
          </Panel>
        ) : (
          <Panel title="文本转换结果" padded>
            <EmptyState
              title={
                dataset && isDatasetRunning(dataset)
                  ? "正在生成知识图谱"
                  : "转换完成后核对实体、关系与本体匹配"
              }
              description="每份文本的任务和处理结果独立保存，可随时返回查看。"
            />
          </Panel>
        )}
      </div>
    </div>
  );
}
