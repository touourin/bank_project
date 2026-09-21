import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Progress, Tabs } from "antd";
import { errorMessage } from "../../api/request";
import { isDemoMode } from "../../demo";
import { useResource } from "../../hooks/useResource";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { Panel } from "../../ui/Panel";
import { knowledgeApi } from "./api";
import { KnowledgeGraphPanel } from "./KnowledgeGraphPanel";
import { MatchingPanel } from "./MatchingPanel";
import { StatusTag, usePolling } from "./shared";
import { GraphChat } from "./GraphChat";
import { ReportsPanel } from "./ReportsPanel";
import "./knowledge.css";

export function GraphRagPage({ token }: { token: string }) {
  const config = useResource(
    useCallback(
      (signal: AbortSignal) => knowledgeApi.config(token, signal),
      [token],
    ),
  );
  const datasets = useResource(
    useCallback(
      (signal: AbortSignal) => knowledgeApi.datasets(token, signal),
      [token],
    ),
    true,
  );
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const dataset = datasets.data?.find((item) => item.key === selected);
  const ready =
    dataset?.status === "succeeded" || dataset?.status === "completed";
  const graph = useResource(
    useCallback(
      (signal: AbortSignal) =>
        selected && ready
          ? knowledgeApi.graph(token, selected, signal)
          : Promise.resolve(null),
      [token, selected, ready],
    ),
  );
  usePolling(
    Boolean(
      datasets.data?.some((item) =>
        ["queued", "running", "indexing"].includes(item.status),
      ),
    ),
    datasets.refresh,
  );
  useEffect(() => {
    if (!selected && datasets.data?.[0]) setSelected(datasets.data[0].key);
  }, [datasets.data, selected]);
  async function start() {
    if (inFlight.current || !selected) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await knowledgeApi.index(token, selected);
      datasets.refresh();
    } catch (reason) {
      setError(errorMessage(reason));
      datasets.refresh();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return (
    <main className="knowledge-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">DATA WORKSPACE / STEP 03</p>
          <h1>GraphRAG 图谱与问答</h1>
          <p className="description">
            从文本抽取实体和关系，查看社区索引、检索证据与节点匹配过程。
          </p>
        </div>
        <span className="step-badge">
          <span>03</span> 文本 · 构图与检索
        </span>
      </div>
      <div className="knowledge-layout">
        <aside className="knowledge-sidebar">
          <Panel
            title="文本数据集"
            description={
              isDemoMode
                ? "示例文本已就绪，选择后即可浏览图谱。"
                : "在数据接入中上传 TXT，随后在这里启动索引。"
            }
            actions={
              <Button size="small" onClick={datasets.refresh}>
                刷新
              </Button>
            }
            padded
          >
            {datasets.loading && !datasets.data && <LoadingState />}
            {datasets.error && (
              <ErrorNotice
                message={datasets.error}
                onRetry={datasets.refresh}
              />
            )}
            {datasets.data?.length === 0 && (
              <EmptyState
                title="暂无文本"
                description="请先在第一步选择 TXT 文本接入。"
              />
            )}
            <div className="knowledge-history">
              {datasets.data?.map((item) => (
                <button
                  key={item.key}
                  aria-pressed={item.key === selected}
                  onClick={() => {
                    setSelected(item.key);
                    setError("");
                  }}
                >
                  <strong>{item.name}</strong>
                  <small>
                    <StatusTag status={item.status} />
                    {item.stage}
                  </small>
                </button>
              ))}
            </div>
          </Panel>
          <Panel title="索引任务" padded>
            {config.error && (
              <ErrorNotice message={config.error} onRetry={config.refresh} />
            )}
            {config.data?.error && (
              <Alert type="warning" showIcon title={config.data.error} />
            )}
            <p className="hint">
              {isDemoMode
                ? "已准备好示例文本的实体、关系和检索证据，可直接体验右侧的图谱浏览、问答与节点匹配。"
                : "GraphRAG 依次执行文本分块、实体关系抽取、社区发现、社区报告和向量索引。开始索引会调用已配置的模型。"}
            </p>
            {dataset && (
              <>
                <p>
                  <strong>{dataset.name}</strong>
                  <small>
                    {dataset.profile === "enterprise_zh"
                      ? "企业情报 · 原项目中文方案（8 类实体）"
                      : "通用文档方案"}
                  </small>
                </p>
                <p>{dataset.stage || "文本已接收"}</p>
                {typeof dataset.progress === "number" ? (
                  <Progress
                    percent={Math.round(dataset.progress * 100)}
                    status={
                      dataset.status === "failed"
                        ? "exception"
                        : ready
                          ? "success"
                          : "active"
                    }
                  />
                ) : (
                  <p className="hint">{dataset.progress}</p>
                )}
                {dataset.error && <ErrorNotice message={dataset.error} />}
              </>
            )}
            <Button
              block
              type="primary"
              loading={busy}
              disabled={
                !dataset ||
                ready ||
                ["queued", "running", "indexing"].includes(dataset.status) ||
                config.data?.configured === false
              }
              onClick={() => void start()}
            >
              {dataset?.status === "failed"
                ? "重试索引"
                : ready
                  ? "索引已完成"
                  : "开始 GraphRAG 索引"}
            </Button>
            {error && <ErrorNotice message={error} />}
          </Panel>
        </aside>
        <div className="knowledge-results">
          {ready && selected ? (
            <Panel
              title={dataset?.name}
              description="图谱、问答与本体匹配使用当前文本数据集。"
              padded
            >
              <Tabs
                items={[
                  {
                    key: "graph",
                    label: "生成图谱",
                    children: graph.error ? (
                      <ErrorNotice
                        message={graph.error}
                        onRetry={graph.refresh}
                      />
                    ) : graph.data ? (
                      <KnowledgeGraphPanel graph={graph.data} />
                    ) : (
                      <LoadingState label="读取 GraphRAG 图谱…" />
                    ),
                  },
                  {
                    key: "query",
                    label: "图谱问答",
                    children: (
                      <GraphChat
                        key={`${token}:${selected}`}
                        token={token}
                        datasetKey={selected}
                        graph={graph.data}
                      />
                    ),
                  },
                  {
                    key: "reports",
                    label: "社区报告",
                    children: (
                      <ReportsPanel token={token} datasetKey={selected} />
                    ),
                  },
                  {
                    key: "matching",
                    label: "节点与边匹配",
                    children: (
                      <MatchingPanel
                        key={`${token}:${selected}`}
                        token={token}
                        sourceId={selected}
                      />
                    ),
                  },
                ]}
              />
            </Panel>
          ) : (
            <Panel title="文本知识空间" padded>
              <EmptyState
                title={
                  dataset &&
                  ["queued", "running", "indexing"].includes(dataset.status)
                    ? "正在生成知识图谱"
                    : "索引完成后即可查看图谱与问答"
                }
                description="每份文本的索引和处理结果独立保存，可随时返回查看。"
              />
            </Panel>
          )}
        </div>
      </div>
    </main>
  );
}
