import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Input, Modal, Select, Tabs } from "antd";
import { errorMessage } from "../../api/request";
import { useResource } from "../../hooks/useResource";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { Panel } from "../../ui/Panel";
import { knowledgeApi } from "./api";
import { KnowledgeGraphPanel } from "./KnowledgeGraphPanel";
import { MatchResults, matchProposalCounts } from "./MatchResults";
import { AuditTable, StatusTag, usePolling } from "./shared";
import type { ConceptDetail, MatchEdge, MatchNode, MatchRun } from "./types";

export function MatchingPanel({
  token,
  sourceId,
}: {
  token: string;
  sourceId: string;
}) {
  const history = useResource(
    useCallback(
      (signal: AbortSignal) => knowledgeApi.matches(token, signal),
      [token],
    ),
    true,
  );
  const sources = useResource(
    useCallback(
      (signal: AbortSignal) => knowledgeApi.sources(token, signal),
      [token],
    ),
    true,
  );
  const [selectedSource, setSelectedSource] = useState(sourceId);
  const [runId, setRunId] = useState("");
  const [updated, setUpdated] = useState<MatchRun>();
  const resource = useResource(
    useCallback(
      (signal: AbortSignal) =>
        runId
          ? knowledgeApi.match(token, runId, signal)
          : Promise.resolve(null),
      [token, runId],
    ),
    true,
  );
  const remote = resource.data?.id === runId ? resource.data : null;
  const run =
    updated?.id === runId && (!remote || updated.revision > remote.revision)
      ? updated
      : remote;
  const proposals = useMemo(
    () => (run ? matchProposalCounts(run) : { nodes: 0, edges: 0 }),
    [run],
  );
  const [busy, setBusy] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const [editing, setEditing] = useState<
    { target: "node"; value: MatchNode } | { target: "edge"; value: MatchEdge }
  >();
  const [selection, setSelection] = useState<string | undefined>();
  const [concepts, setConcepts] = useState<ConceptDetail[]>([]);
  const [searching, setSearching] = useState(false);
  const [reviewer, setReviewer] = useState("");
  const [note, setNote] = useState("");
  const searchRequest = useRef<AbortController | undefined>(undefined);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const graph = useResource(
    useCallback(
      (signal: AbortSignal) =>
        run?.status === "ready"
          ? knowledgeApi.matchGraph(token, run.id, signal)
          : Promise.resolve(null),
      [token, run?.id, run?.status, run?.revision],
    ),
  );
  usePolling(run?.status === "running", resource.refresh);
  useEffect(() => {
    if (!runId) {
      const item = history.data?.find(
        (entry) => entry.source_id === selectedSource,
      );
      if (item) setRunId(item.id);
    }
  }, [history.data, runId, selectedSource]);
  useEffect(() => {
    if (run?.status === "ready" || run?.status === "failed") history.refresh();
  }, [run?.id, run?.status, history.refresh]);
  useEffect(
    () => () => {
      searchRequest.current?.abort();
      clearTimeout(searchTimer.current);
    },
    [],
  );
  async function start() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const created = await knowledgeApi.matchStart(token, selectedSource);
      setRunId(created.id);
      setUpdated(created);
      history.refresh();
    } catch (reason) {
      setError(errorMessage(reason));
      history.refresh();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  function edit(target: typeof editing) {
    searchRequest.current?.abort();
    clearTimeout(searchTimer.current);
    setSearching(false);
    setEditing(target);
    setSelection(
      target?.target === "node"
        ? (target.value.boid ?? undefined)
        : (target?.value.edge_type ?? undefined),
    );
    setConcepts(target?.target === "node" ? target.value.trace.candidates : []);
    setNote("");
    setError("");
  }
  function search(q: string) {
    if (!run) return;
    searchRequest.current?.abort();
    clearTimeout(searchTimer.current);
    const controller = new AbortController();
    searchRequest.current = controller;
    setSearching(true);
    searchTimer.current = setTimeout(async () => {
      try {
        const values = await knowledgeApi.concepts(
          token,
          run.id,
          q,
          controller.signal,
        );
        if (!controller.signal.aborted) setConcepts(values);
      } catch (reason) {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      } finally {
        if (!controller.signal.aborted) setSearching(false);
      }
    }, 250);
  }
  async function save() {
    if (!run || !editing || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await knowledgeApi.matchDecision(token, run.id, {
        target: editing.target,
        target_id: editing.value.id,
        expected_revision: run.revision,
        ...(editing.target === "node"
          ? { boid: selection ?? null }
          : { edge_type: selection ?? null }),
        reviewer: reviewer.trim() || undefined,
        note,
      });
      setUpdated(result);
      setEditing(undefined);
      resource.refresh();
      history.refresh();
      sources.refresh();
    } catch (reason) {
      setError(errorMessage(reason));
      setUpdated(undefined);
      resource.refresh();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  async function acceptProposals() {
    if (!run || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setAccepting(true);
    setError("");
    try {
      const result = await knowledgeApi.matchAccept(token, run.id, {
        expected_revision: run.revision,
      });
      setUpdated(result);
      resource.refresh();
      history.refresh();
      sources.refresh();
    } catch (reason) {
      setError(errorMessage(reason));
      setUpdated(undefined);
      resource.refresh();
    } finally {
      inFlight.current = false;
      setBusy(false);
      setAccepting(false);
    }
  }
  const conceptOptions = concepts.map((concept) => ({
    value: concept.id,
    label: `${concept.name} · ${concept.id}${concept.score != null ? ` · ${concept.score.toFixed(3)}` : ""}`,
  }));
  if (
    selection &&
    editing?.target === "node" &&
    !conceptOptions.some((item) => item.value === selection)
  )
    conceptOptions.unshift({ value: selection, label: selection });
  return (
    <div>
      <Alert
        type="info"
        showIcon
        title="复用本体对齐与图谱生成的匹配流程"
        description="自动展示节点与边的匹配建议，核对后可整体采纳，也可按需修改。节点 ID、名称、原始类型、属性和边端点均保留。"
      />
      <div className="knowledge-toolbar">
        <label className="knowledge-field" style={{ flex: 1, marginBottom: 0 }}>
          待匹配图谱版本
          <Select
            aria-label="待匹配图谱版本"
            value={selectedSource}
            disabled={busy || run?.status === "running"}
            onChange={(value) => {
              setSelectedSource(value);
              setRunId("");
              setUpdated(undefined);
            }}
            options={[
              { value: sourceId, label: "原始 GraphRAG 图谱" },
              ...(sources.data ?? [])
                .filter(
                  (item) =>
                    item.kind === "graphrag" &&
                    item.id !== sourceId &&
                    item.root_source_id === sourceId &&
                    !item.error,
                )
                .map((item) => ({ value: item.id, label: item.name })),
            ]}
          />
        </label>
        <Button onClick={sources.refresh}>刷新图谱版本</Button>
      </div>
      <p className="hint">
        可选择消歧结果继续挂载本体标签，后续处理保留所选版本已经完成的合并与匹配。
      </p>
      {sources.error && (
        <ErrorNotice message={sources.error} onRetry={sources.refresh} />
      )}
      <div className="knowledge-toolbar">
        <Select
          aria-label="匹配历史"
          value={runId || undefined}
          placeholder="选择匹配历史"
          style={{ minWidth: 220, maxWidth: "100%" }}
          disabled={busy}
          onChange={(value) => {
            setRunId(value);
            setUpdated(undefined);
          }}
          options={history.data
            ?.filter((entry) => entry.source_id === selectedSource)
            .map((entry) => ({
              value: entry.id,
              label: `${new Date(entry.created_at).toLocaleString()} · ${entry.name}`,
            }))}
        />
        <Button
          type="primary"
          loading={busy && !accepting && !editing}
          disabled={busy || run?.status === "running"}
          onClick={() => void start()}
        >
          开始节点与边匹配
        </Button>
      </div>
      {(error || resource.error || history.error) && (
        <ErrorNotice
          message={error || resource.error || history.error || ""}
          onRetry={() => {
            resource.refresh();
            history.refresh();
          }}
        />
      )}
      {run?.error && <ErrorNotice message={run.error} />}
      {!run &&
        (resource.loading && runId ? (
          <LoadingState />
        ) : (
          <EmptyState
            title="尚未生成匹配结果"
            description="开始匹配后查看每个节点的检索候选、得分及边类型建议。"
          />
        ))}
      {run && (
        <>
          <div className="knowledge-toolbar">
            <span>
              <StatusTag status={run.status} />
              {run.progress}
            </span>
            <span className="hint">修订 {run.revision}</span>
          </div>
          {run.status === "running" && (
            <LoadingState label="正在检索本体并匹配节点与关系…" />
          )}
          <div className="knowledge-metrics">
            <div>
              <span>节点挂载</span>
              <strong>
                {run.summary.matched_nodes} / {run.summary.node_count}
              </strong>
            </div>
            <div>
              <span>待采纳节点建议</span>
              <strong>{proposals.nodes}</strong>
            </div>
            <div>
              <span>边类型挂载</span>
              <strong>
                {run.summary.matched_edges} / {run.summary.edge_count}
              </strong>
            </div>
          </div>
          <Panel
            className="knowledge-matching-panel"
            title="匹配结果"
            eyebrow="核对匹配方案"
            description="自动展示节点与关系的本体匹配建议。低分候选可整体采纳，匹配有误时按需修改；展开行可查看匹配过程与检索依据。"
            padded
            actions={
              <Button
                type="primary"
                loading={accepting}
                aria-label="整体采纳匹配建议"
                disabled={
                  run.status !== "ready" ||
                  busy ||
                  proposals.nodes + proposals.edges === 0
                }
                onClick={() => void acceptProposals()}
              >
                整体采纳匹配建议
              </Button>
            }
          >
            <details className="mapping-metadata">
              <summary>本体版本与匹配说明</summary>
              <p className="hint ontology-revision">
                本体版本：
                <code>{run.ontology_revision || "历史任务未记录"}</code>
              </p>
              <p className="hint">
                节点复用本体对齐的 retrieve 检索、候选得分与版本校验。
                {run.confidence_threshold != null
                  ? `自动采用阈值：${run.confidence_threshold.toFixed(3)}。`
                  : "历史任务未记录自动采用阈值。"}
                原检索得分不代表正确概率。
              </p>
              <p className="hint">
                整体采纳会挂载有效的节点建议，并重新校验边的方向与类型；多种关系候选需手动选择。已人工修改或清除的结果保持不变，未匹配对象保留原始信息。
              </p>
            </details>
            <Tabs
              items={[
                {
                  key: "nodes",
                  label: "节点匹配过程",
                  children: (
                    <MatchResults
                      key={`${run.id}:nodes`}
                      run={run}
                      target="node"
                      disabled={run.status !== "ready" || busy}
                      onEdit={edit}
                    />
                  ),
                },
                {
                  key: "edges",
                  label: "边类型匹配",
                  children: (
                    <MatchResults
                      key={`${run.id}:edges`}
                      run={run}
                      target="edge"
                      disabled={run.status !== "ready" || busy}
                      onEdit={edit}
                    />
                  ),
                },
                {
                  key: "audits",
                  label: `人工修改记录（${run.audits.length}）`,
                  children: <AuditTable audits={run.audits} />,
                },
                {
                  key: "graph",
                  label: "匹配后图谱",
                  children: graph.error ? (
                    <ErrorNotice
                      message={graph.error}
                      onRetry={graph.refresh}
                    />
                  ) : graph.data ? (
                    <KnowledgeGraphPanel graph={graph.data} />
                  ) : (
                    <EmptyState title="匹配完成后展示图谱" />
                  ),
                },
              ]}
            />
          </Panel>
        </>
      )}
      <Modal
        title={editing?.target === "node" ? "修改节点 boid" : "修改边类型"}
        open={Boolean(editing)}
        onCancel={() => {
          if (!busy) edit(undefined);
        }}
        onOk={() => void save()}
        confirmLoading={busy}
        okText="保存修改"
        cancelText="取消"
        destroyOnHidden
      >
        <p className="hint">原始元素 ID：{editing?.value.id}</p>
        <label className="knowledge-field">
          {editing?.target === "node" ? "本体概念（可搜索）" : "关系类型"}
          <Select
            aria-label={
              editing?.target === "node" ? "选择本体概念" : "选择边类型"
            }
            allowClear
            showSearch
            filterOption={editing?.target !== "node"}
            loading={searching}
            onSearch={editing?.target === "node" ? search : undefined}
            value={selection}
            onChange={setSelection}
            placeholder="清除选择可移除挂载"
            options={
              editing?.target === "node"
                ? conceptOptions
                : editing?.value.candidates.map((value) => ({
                    value,
                    label: value,
                  }))
            }
          />
        </label>
        <label className="knowledge-field">
          审核人
          <Input
            value={reviewer}
            maxLength={100}
            onChange={(event) => setReviewer(event.target.value)}
          />
        </label>
        <label className="knowledge-field">
          修改原因
          <Input.TextArea
            value={note}
            maxLength={2000}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
        {error && <ErrorNotice message={error} />}
      </Modal>
    </div>
  );
}
