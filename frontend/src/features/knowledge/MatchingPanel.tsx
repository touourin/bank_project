import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Modal, Select, Table, Tabs } from "antd";
import { errorMessage } from "../../api/request";
import { useResource } from "../../hooks/useResource";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { knowledgeApi } from "./api";
import { KnowledgeGraphPanel } from "./KnowledgeGraphPanel";
import { AuditTable, JsonDetails, StatusTag, usePolling } from "./shared";
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
  const [busy, setBusy] = useState(false);
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
    } catch (reason) {
      setError(errorMessage(reason));
      setUpdated(undefined);
      resource.refresh();
    } finally {
      inFlight.current = false;
      setBusy(false);
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
        title="复用现有本体检索与匹配流程"
        description="仅挂载节点 boid 和边类型；保留节点 ID、名称、原始类型、所有属性以及边端点。低分结果可人工修订或清除挂载。"
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
          loading={busy}
          disabled={run?.status === "running"}
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
            <span className="hint">
              本体版本 {run.ontology_revision || "—"} · 修订 {run.revision}
            </span>
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
              <span>边类型挂载</span>
              <strong>
                {run.summary.matched_edges} / {run.summary.edge_count}
              </strong>
            </div>
          </div>
          <Tabs
            items={[
              {
                key: "nodes",
                label: "节点匹配过程",
                children: (
                  <Table
                    rowKey="id"
                    size="small"
                    dataSource={run.nodes}
                    scroll={{ x: 650 }}
                    pagination={{ pageSize: 10 }}
                    columns={[
                      { title: "原始节点", dataIndex: "name" },
                      {
                        title: "挂载 boid",
                        dataIndex: "boid",
                        render: (value: string) => value || "未挂载",
                      },
                      {
                        title: "检索结果",
                        render: (_, node) => (
                          <StatusTag status={node.trace.status} />
                        ),
                      },
                      {
                        title: "人工调整",
                        render: (_, node) => (
                          <Button
                            disabled={run.status !== "ready" || busy}
                            size="small"
                            onClick={() =>
                              edit({ target: "node", value: node })
                            }
                          >
                            修改节点
                          </Button>
                        ),
                      },
                    ]}
                    expandable={{
                      expandedRowRender: (node) => (
                        <div>
                          <p>
                            <strong>检索语义：</strong>
                            {node.trace.query}
                          </p>
                          <p>{node.trace.detail}</p>
                          <Table
                            rowKey="id"
                            size="small"
                            dataSource={node.trace.candidates}
                            pagination={false}
                            locale={{ emptyText: "未找到本体候选" }}
                            columns={[
                              { title: "候选概念", dataIndex: "name" },
                              { title: "boid", dataIndex: "id" },
                              {
                                title: "原检索得分",
                                dataIndex: "score",
                                render: (value: number) =>
                                  value == null ? "—" : value.toFixed(3),
                              },
                            ]}
                          />
                          <JsonDetails
                            value={node.trace}
                            label="完整匹配过程"
                          />
                        </div>
                      ),
                    }}
                  />
                ),
              },
              {
                key: "edges",
                label: "边类型匹配",
                children: (
                  <Table
                    rowKey="id"
                    size="small"
                    dataSource={run.edges}
                    scroll={{ x: 650 }}
                    pagination={{ pageSize: 10 }}
                    columns={[
                      { title: "起点", dataIndex: "source", ellipsis: true },
                      { title: "终点", dataIndex: "target", ellipsis: true },
                      {
                        title: "边类型",
                        dataIndex: "edge_type",
                        render: (value: string) => value || "未挂载",
                      },
                      {
                        title: "状态",
                        render: (_, edge) => <StatusTag status={edge.status} />,
                      },
                      {
                        title: "人工调整",
                        render: (_, edge) => (
                          <Button
                            size="small"
                            disabled={run.status !== "ready" || busy}
                            onClick={() =>
                              edit({ target: "edge", value: edge })
                            }
                          >
                            修改边类型
                          </Button>
                        ),
                      },
                    ]}
                    expandable={{
                      expandedRowRender: (edge) => (
                        <>
                          <p>{edge.detail}</p>
                          <p>
                            可选边类型：
                            {edge.candidates.join("、") || "暂无候选"}
                          </p>
                          <JsonDetails value={edge} label="完整关系匹配信息" />
                        </>
                      ),
                    }}
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
                  <ErrorNotice message={graph.error} onRetry={graph.refresh} />
                ) : graph.data ? (
                  <KnowledgeGraphPanel graph={graph.data} />
                ) : (
                  <EmptyState title="匹配完成后展示图谱" />
                ),
              },
            ]}
          />
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
