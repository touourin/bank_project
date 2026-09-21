import { useEffect, useRef, useState } from "react";
import { Button, Input, Select, Tabs } from "antd";
import { errorMessage } from "../../api/request";
import { isDemoMode } from "../../demo";
import { ErrorNotice, LoadingState } from "../../ui/Feedback";
import { streamQuery, knowledgeApi } from "./api";
import { KnowledgeGraphPanel } from "./KnowledgeGraphPanel";
import { JsonDetails } from "./shared";
import type { KnowledgeGraph, QueryResult } from "./types";

const methods = [
  { value: "local", label: "Local · 实体与关系" },
  { value: "global", label: "Global · 全局主题" },
  { value: "drift", label: "DRIFT · 扩展检索" },
  { value: "basic", label: "Basic · 原文向量检索" },
];
export function GraphChat({
  token,
  datasetKey,
  graph,
}: {
  token: string;
  datasetKey: string;
  graph?: KnowledgeGraph | null;
}) {
  const [question, setQuestion] = useState(
    isDemoMode ? "图谱中有哪些企业关联关系？" : "",
  );
  const [method, setMethod] = useState("local");
  const [history, setHistory] = useState<
    { question: string; method: string; result: QueryResult }[]
  >([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [suggesting, setSuggesting] = useState(false);
  const [selected, setSelected] = useState(0);
  const [partial, setPartial] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => active.current?.abort(), []);
  const current = history[selected];
  async function ask(compare = false) {
    if (active.current || !question.trim()) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    setPartial("");
    let nextIndex = history.length;
    try {
      for (const mode of compare ? methods.map((m) => m.value) : [method]) {
        setPartial("");
        const result = await streamQuery(
          token,
          datasetKey,
          question.trim(),
          mode,
          (text) => setPartial((prev) => prev + text),
          controller.signal,
        );
        if (controller.signal.aborted) break;
        setHistory((prev) => [
          ...prev,
          { question: question.trim(), method: mode, result },
        ]);
        setSelected(nextIndex++);
        setPartial("");
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(errorMessage(reason));
    } finally {
      active.current = undefined;
      setBusy(false);
    }
  }
  async function suggest() {
    setSuggesting(true);
    setError("");
    try {
      const result = await knowledgeApi.questions(token, datasetKey, question);
      setSuggestions(
        result.answer
          .split("\n")
          .map((s) => s.replace(/^\s*[-*\d.、]+\s*/, ""))
          .filter(Boolean),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSuggesting(false);
    }
  }
  return (
    <div className="graph-chat-layout">
      <div className="knowledge-query">
        <p className="hint">
          回答后自动显示答案依据子图。Local / Global / Basic 流式输出；DRIFT
          多轮检索完成后返回。问答依据原始 GraphRAG 索引。
        </p>
        <label className="knowledge-field">
          检索方式
          <Select
            aria-label="检索方式"
            value={method}
            onChange={setMethod}
            options={methods}
            disabled={busy}
          />
        </label>
        <label className="knowledge-field">
          你的问题
          <Input.TextArea
            aria-label="你的问题"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={16000}
            disabled={busy}
            autoSize={{ minRows: 3, maxRows: 8 }}
          />
        </label>
        <div className="knowledge-review-actions">
          <Button
            aria-label="查询图谱"
            type="primary"
            loading={busy}
            disabled={!question.trim()}
            onClick={() => void ask()}
          >
            查询图谱
          </Button>
          <Button
            loading={suggesting}
            disabled={busy}
            onClick={() => void suggest()}
          >
            生成推荐问题
          </Button>
          <Button
            disabled={busy || !question.trim()}
            onClick={() => void ask(true)}
          >
            四种方法对比
          </Button>
          {busy && (
            <Button onClick={() => active.current?.abort()}>停止回答</Button>
          )}
        </div>
        {suggestions.map((q) => (
          <Button key={q} type="link" onClick={() => setQuestion(q)}>
            {q}
          </Button>
        ))}
        {!history.length && (
          <div className="knowledge-review-actions">
            {[
              "概览这份文本的主要内容",
              ...(graph?.nodes
                .slice(0, 2)
                .map((n) => `${n.name}与哪些实体存在关系？`) ?? []),
            ].map((q) => (
              <Button key={q} size="small" onClick={() => setQuestion(q)}>
                {q}
              </Button>
            ))}
          </div>
        )}
        {busy && (
          <LoadingState
            label={partial ? "正在生成回答…" : "正在检索图谱并生成回答…"}
          />
        )}
        {partial && (
          <div className="knowledge-answer" aria-live="polite">
            {partial}
          </div>
        )}
        {error && <ErrorNotice message={error} />}
        {!!history.length && (
          <Tabs
            destroyOnHidden
            activeKey={String(selected)}
            onChange={(k) => setSelected(Number(k))}
            items={history.map((entry, i) => ({
              key: String(i),
              label: `${i + 1} · ${entry.method.toUpperCase()}`,
              children: (
                <>
                  <strong>{entry.question}</strong>
                  <div className="knowledge-answer">{entry.result.answer}</div>
                  <JsonDetails
                    value={entry.result.context}
                    label="检索证据与上下文"
                  />
                  <JsonDetails
                    value={
                      entry.result.trace ?? {
                        method: entry.method,
                        elapsed_seconds: entry.result.elapsed_seconds,
                      }
                    }
                    label="检索过程"
                  />
                </>
              ),
            }))}
          />
        )}
      </div>
      <div>
        {graph ? (
          <KnowledgeGraphPanel
            graph={graph}
            evidence={current?.result.evidence}
          />
        ) : (
          <LoadingState label="读取图谱…" />
        )}
      </div>
    </div>
  );
}
