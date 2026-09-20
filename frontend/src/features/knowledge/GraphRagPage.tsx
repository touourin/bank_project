import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Progress, Select, Tabs } from "antd";
import { errorMessage } from "../../api/request";
import { useResource } from "../../hooks/useResource";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { Panel } from "../../ui/Panel";
import { knowledgeApi } from "./api";
import { KnowledgeGraphPanel } from "./KnowledgeGraphPanel";
import { MatchingPanel } from "./MatchingPanel";
import { JsonDetails, StatusTag, usePolling } from "./shared";
import type { QueryResult } from "./types";
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
            description="在数据接入中上传 TXT，随后在这里启动索引。"
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
              GraphRAG
              依次执行文本分块、实体关系抽取、社区发现、社区报告和向量索引。开始索引会调用已配置的模型。
            </p>
            {dataset && (
              <>
                <p>
                  <strong>{dataset.name}</strong>
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
                      <QuestionPanel
                        key={`${token}:${selected}`}
                        token={token}
                        datasetKey={selected}
                      />
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

function QuestionPanel({
  token,
  datasetKey,
}: {
  token: string;
  datasetKey: string;
}) {
  const [question, setQuestion] = useState("");
  const [method, setMethod] = useState("local");
  const [result, setResult] = useState<QueryResult>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => active.current?.abort(), []);
  async function ask() {
    if (active.current || !question.trim()) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    setResult(undefined);
    try {
      const value = await knowledgeApi.query(
        token,
        datasetKey,
        question.trim(),
        method,
        controller.signal,
      );
      if (!controller.signal.aborted) setResult(value);
    } catch (reason) {
      if (!controller.signal.aborted) setError(errorMessage(reason));
    } finally {
      if (!controller.signal.aborted) {
        active.current = undefined;
        setBusy(false);
      }
    }
  }
  return (
    <div className="knowledge-query">
      <p className="hint">
        Local 聚焦具体实体；Global 基于社区报告回答全局问题；DRIFT
        扩展相关问题后综合回答。问答依据原始 GraphRAG
        索引，人工消歧和本体标签不会重写检索证据。
      </p>
      <label className="knowledge-field">
        检索方式
        <Select
          aria-label="检索方式"
          value={method}
          disabled={busy}
          onChange={setMethod}
          options={[
            { value: "local", label: "Local · 实体与关系" },
            { value: "global", label: "Global · 全局主题" },
            { value: "drift", label: "DRIFT · 扩展检索" },
          ]}
        />
      </label>
      <label className="knowledge-field">
        你的问题
        <Input.TextArea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          autoSize={{ minRows: 3, maxRows: 8 }}
          maxLength={10000}
          disabled={busy}
          placeholder="例如：文中哪些企业存在关联，它们的关系是什么？"
        />
      </label>
      <Button
        aria-label="查询图谱"
        type="primary"
        loading={busy}
        disabled={!question.trim()}
        onClick={() => void ask()}
      >
        查询图谱
      </Button>
      {busy && <LoadingState label="正在检索图谱并生成回答…" />}
      {error && <ErrorNotice message={error} />}
      {result && (
        <>
          <div className="knowledge-answer" aria-live="polite">
            {result.answer}
          </div>
          <JsonDetails value={result.context} label="检索证据与上下文" />
        </>
      )}
    </div>
  );
}
