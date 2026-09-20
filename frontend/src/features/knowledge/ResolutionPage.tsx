import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Input,
  Modal,
  Pagination,
  Select,
  Table,
  Tabs,
} from "antd";
import { errorMessage } from "../../api/request";
import { useResource } from "../../hooks/useResource";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { Panel } from "../../ui/Panel";
import { knowledgeApi } from "./api";
import { KnowledgeGraphPanel } from "./KnowledgeGraphPanel";
import {
  AuditTable,
  JsonDetails,
  pretty,
  StatusTag,
  usePolling,
} from "./shared";
import type {
  KnowledgeNode,
  ResolutionCandidate,
  ResolutionRun,
} from "./types";
import "./knowledge.css";

export function ResolutionPage({ token }: { token: string }) {
  const sources = useResource(
    useCallback(
      (signal: AbortSignal) => knowledgeApi.sources(token, signal),
      [token],
    ),
    true,
  );
  const history = useResource(
    useCallback(
      (signal: AbortSignal) => knowledgeApi.resolutions(token, signal),
      [token],
    ),
    true,
  );
  const [source, setSource] = useState("");
  const [runId, setRunId] = useState("");
  const [updated, setUpdated] = useState<ResolutionRun>();
  const resource = useResource(
    useCallback(
      (signal: AbortSignal) =>
        runId
          ? knowledgeApi.resolution(token, runId, signal)
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
  const graph = useResource(
    useCallback(
      (signal: AbortSignal) =>
        run?.status === "ready"
          ? knowledgeApi.resolutionGraph(token, run.id, signal)
          : Promise.resolve(null),
      [token, run?.id, run?.status, run?.revision],
    ),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const [reviewer, setReviewer] = useState("");
  const [note, setNote] = useState("");
  const [filter, setFilter] = useState("pending");
  const [candidatePage, setCandidatePage] = useState(1);
  useEffect(() => setCandidatePage(1), [filter, runId]);
  const [manual, setManual] = useState(false);
  const [manualNodes, setManualNodes] = useState<string[]>([]);
  const [canonical, setCanonical] = useState<string>();
  usePolling(run?.status === "analyzing", resource.refresh);
  useEffect(() => {
    if (!source) {
      const first = sources.data?.find((item) => item.id && !item.error);
      if (first) setSource(`${first.kind}:${first.id}`);
    }
  }, [sources.data, source]);
  useEffect(() => {
    if (!runId && history.data?.[0]) setRunId(history.data[0].id);
  }, [history.data, runId]);
  useEffect(() => {
    if (run?.status === "ready" || run?.status === "failed") history.refresh();
  }, [run?.id, run?.status, history.refresh]);
  async function mutate(action: () => Promise<ResolutionRun>, created = false) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const value = await action();
      setUpdated(value);
      if (created) {
        setRunId(value.id);
        setFilter("pending");
      } else resource.refresh();
      setManual(false);
      setNote("");
      history.refresh();
    } catch (reason) {
      setError(errorMessage(reason));
      setUpdated(undefined);
      resource.refresh();
      history.refresh();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  function start() {
    const selected = sources.data?.find(
      (item) => `${item.kind}:${item.id}` === source,
    );
    if (selected)
      void mutate(
        () => knowledgeApi.resolve(token, selected.kind, selected.id),
        true,
      );
  }
  function decision(
    candidate: ResolutionCandidate,
    action: "merge" | "reject" | "reset",
    canonical_id?: string,
  ) {
    if (!run) return;
    void mutate(() =>
      knowledgeApi.resolutionDecision(token, run.id, {
        candidate_id: candidate.id,
        action,
        canonical_id,
        expected_revision: run.revision,
        reviewer: reviewer.trim() || undefined,
        note,
      }),
    );
  }
  const candidates =
    run?.candidates.filter(
      (candidate) => filter === "all" || candidate.status === filter,
    ) ?? [];
  const page = Math.min(
    candidatePage,
    Math.max(1, Math.ceil(candidates.length / 10)),
  );
  const nodeOptions =
    graph.data?.nodes.map((node) => ({
      value: node.id,
      label: `${node.name} · ${node.id}`,
    })) ?? [];
  return (
    <main className="knowledge-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">DATA WORKSPACE / STEP 04</p>
          <h1>实体消歧</h1>
          <p className="description">
            统一核验文本图谱与数据库图谱中的重复实体，保留每次合并的证据和过程。
          </p>
        </div>
        <span className="step-badge">
          <span>04</span> 实体 · 校验与合并
        </span>
      </div>
      <div className="knowledge-layout">
        <aside className="knowledge-sidebar">
          <Panel
            title="选择图谱来源"
            actions={
              <Button size="small" onClick={sources.refresh}>
                刷新来源
              </Button>
            }
            padded
          >
            {sources.error && (
              <ErrorNotice message={sources.error} onRetry={sources.refresh} />
            )}
            {sources.data
              ?.filter((item) => item.error)
              .map((item) => (
                <Alert
                  key={`${item.kind}:${item.id}`}
                  type="warning"
                  showIcon
                  title={item.name}
                  description={item.error}
                />
              ))}
            {sources.loading && !sources.data && <LoadingState />}
            <label className="knowledge-field">
              待消歧图谱
              <Select
                aria-label="待消歧图谱"
                value={source || undefined}
                onChange={setSource}
                options={sources.data?.map((item) => ({
                  value: `${item.kind}:${item.id}`,
                  label: `${item.kind === "graphrag" ? "GraphRAG" : "数据库"} · ${item.name}`,
                  disabled: !item.id || Boolean(item.error),
                }))}
                placeholder="选择文本或数据库生成的图谱"
              />
            </label>
            {sources.data?.length === 0 && (
              <p className="hint">请先完成 GraphRAG 索引或数据库图谱生成。</p>
            )}
            <Button
              type="primary"
              block
              loading={busy}
              disabled={!source || run?.status === "analyzing"}
              onClick={start}
            >
              分析消歧候选
            </Button>
            <p className="hint">
              分析生成候选和独立图谱快照。人工核验身份、证据和属性冲突后决定合并或保留，可撤销审核决定。
            </p>
          </Panel>
          <Panel
            title="消歧历史"
            actions={
              <Button size="small" onClick={history.refresh}>
                刷新
              </Button>
            }
            padded
          >
            {history.error && (
              <ErrorNotice message={history.error} onRetry={history.refresh} />
            )}
            <div className="knowledge-history">
              {history.data?.map((item) => (
                <button
                  key={item.id}
                  aria-pressed={item.id === runId}
                  disabled={busy}
                  onClick={() => {
                    setRunId(item.id);
                    setUpdated(undefined);
                    setError("");
                  }}
                >
                  <strong>{item.name}</strong>
                  <small>
                    {item.source_kind === "graphrag" ? "GraphRAG" : "数据库"} ·{" "}
                    {new Date(item.created_at).toLocaleString()}
                  </small>
                  <small>
                    <StatusTag status={item.status} />
                  </small>
                </button>
              ))}
            </div>
            {history.data?.length === 0 && <EmptyState title="暂无消歧任务" />}
          </Panel>
        </aside>
        <div className="knowledge-results">
          {(error || resource.error) && (
            <ErrorNotice
              message={error || resource.error || ""}
              onRetry={resource.refresh}
            />
          )}
          {run ? (
            <>
              <Panel
                title={run.name}
                description={`${run.source_kind === "graphrag" ? "GraphRAG 文本图谱" : "数据库图谱"} · 修订 ${run.revision}`}
                padded
              >
                <div className="knowledge-toolbar">
                  <span>
                    <StatusTag status={run.status} />
                    {run.progress}
                  </span>
                  <Button
                    disabled={run.status !== "ready" || busy || !graph.data}
                    onClick={() => {
                      setManualNodes([]);
                      setCanonical(undefined);
                      setManual(true);
                    }}
                  >
                    人工指定合并
                  </Button>
                </div>
                {run.error && <ErrorNotice message={run.error} />}
                {run.diagnostics.warnings?.map((warning, index) => (
                  <Alert key={index} type="warning" showIcon title={warning} />
                ))}
                {run.status === "analyzing" && (
                  <LoadingState label="正在计算实体候选及冲突…" />
                )}
                <div className="knowledge-metrics">
                  <div>
                    <span>节点变化</span>
                    <strong>
                      {run.summary.original_node_count} →{" "}
                      {run.summary.node_count}
                    </strong>
                  </div>
                  <div>
                    <span>待核验</span>
                    <strong>{run.summary.pending_count}</strong>
                  </div>
                  <div>
                    <span>已合并 / 已保留</span>
                    <strong>
                      {run.summary.merged_count} / {run.summary.rejected_count}
                    </strong>
                  </div>
                </div>
                {run.source_kind === "graphrag" && (
                  <p className="hint">
                    消歧图谱单独保存。GraphRAG
                    问答继续使用原始文本索引及社区报告。
                  </p>
                )}
                <div className="knowledge-form-row">
                  <label className="knowledge-field">
                    审核人
                    <Input
                      aria-label="消歧审核人"
                      value={reviewer}
                      maxLength={200}
                      onChange={(event) => setReviewer(event.target.value)}
                      placeholder="可选"
                    />
                  </label>
                  <label className="knowledge-field">
                    审核备注
                    <Input
                      aria-label="消歧审核备注"
                      value={note}
                      maxLength={4000}
                      onChange={(event) => setNote(event.target.value)}
                      placeholder="用于下一次合并、保留或撤销操作"
                    />
                  </label>
                </div>
                <Tabs
                  items={[
                    {
                      key: "candidates",
                      label: `候选核验（${run.candidates.length}）`,
                      children: (
                        <>
                          <div className="knowledge-toolbar">
                            <Select
                              aria-label="候选状态"
                              value={filter}
                              onChange={setFilter}
                              options={[
                                { value: "pending", label: "待核验" },
                                { value: "merged", label: "已合并" },
                                { value: "rejected", label: "已保留" },
                                { value: "all", label: "全部候选" },
                              ]}
                            />
                            <span className="hint">
                              合并保留指定节点 ID，冲突信息随结果保留。
                            </span>
                          </div>
                          {candidates.length ? (
                            candidates
                              .slice((page - 1) * 10, page * 10)
                              .map((candidate) => (
                                <CandidateCard
                                  key={`${run.id}:${candidate.id}:${candidate.status}:${candidate.canonical_id}`}
                                  candidate={candidate}
                                  disabled={busy || run.status !== "ready"}
                                  onDecision={decision}
                                />
                              ))
                          ) : (
                            <EmptyState
                              title="当前没有此类候选"
                              description="未自动召回的实体可通过“人工指定合并”核验。"
                            />
                          )}
                          {candidates.length > 10 && (
                            <Pagination
                              current={page}
                              pageSize={10}
                              total={candidates.length}
                              onChange={setCandidatePage}
                              showSizeChanger={false}
                              showTotal={(total) => `共 ${total} 组候选`}
                            />
                          )}
                        </>
                      ),
                    },
                    {
                      key: "merges",
                      label: `合并过程（${run.merges.length}）`,
                      children: run.merges.length ? (
                        run.merges.map((merge) => (
                          <div
                            key={merge.candidate_id}
                            className="resolution-candidate"
                          >
                            <h3>已合并 {merge.source_nodes.length} 个实体</h3>
                            <MergeFlow
                              nodes={merge.source_nodes}
                              target={merge.target_node}
                            />
                          </div>
                        ))
                      ) : (
                        <EmptyState title="尚无合并记录" />
                      ),
                    },
                    {
                      key: "audits",
                      label: `审核历史（${run.audits.length}）`,
                      children: <AuditTable audits={run.audits} />,
                    },
                    {
                      key: "graph",
                      label: "消歧后图谱",
                      children: graph.error ? (
                        <ErrorNotice
                          message={graph.error}
                          onRetry={graph.refresh}
                        />
                      ) : graph.data ? (
                        <KnowledgeGraphPanel graph={graph.data} />
                      ) : (
                        <EmptyState title="分析完成后展示图谱" />
                      ),
                    },
                  ]}
                />
                <JsonDetails value={run.diagnostics} label="候选生成诊断" />
              </Panel>
            </>
          ) : (
            <Panel title="实体核验空间" padded>
              {runId && resource.loading ? (
                <LoadingState />
              ) : (
                <EmptyState
                  title="选择图谱并分析消歧候选"
                  description="支持 GraphRAG 文本图谱与现有数据库路径生成的图谱。"
                />
              )}
            </Panel>
          )}
        </div>
      </div>
      <Modal
        title="人工指定实体合并"
        open={manual}
        onCancel={() => {
          if (!busy) setManual(false);
        }}
        confirmLoading={busy}
        okText="确认合并"
        cancelText="取消"
        okButtonProps={{ disabled: manualNodes.length < 2 || !canonical }}
        onOk={() => {
          if (run && canonical)
            void mutate(() =>
              knowledgeApi.manualMerge(token, run.id, {
                node_ids: manualNodes,
                canonical_id: canonical,
                expected_revision: run.revision,
                reviewer: reviewer.trim() || undefined,
                note,
              }),
            );
        }}
        width={680}
      >
        <Alert
          type="info"
          showIcon
          title="请选择表示同一真实实体的节点"
          description="合并后保留一个节点 ID，其他节点的来源和属性冲突会被记录。"
        />
        <label className="knowledge-field">
          需要合并的节点
          <Select
            aria-label="人工合并节点"
            mode="multiple"
            showSearch
            optionFilterProp="label"
            value={manualNodes}
            options={nodeOptions}
            onChange={(values: string[]) => {
              setManualNodes(values);
              if (!canonical || !values.includes(canonical))
                setCanonical(values[0]);
            }}
          />
        </label>
        <label className="knowledge-field">
          保留节点
          <Select
            aria-label="人工合并保留节点"
            value={canonical}
            onChange={setCanonical}
            options={nodeOptions.filter((node) =>
              manualNodes.includes(node.value),
            )}
          />
        </label>
        {manualNodes.length > 0 && (
          <MergeFlow
            nodes={
              graph.data?.nodes.filter((node) =>
                manualNodes.includes(node.id),
              ) ?? []
            }
            target={graph.data?.nodes.find((node) => node.id === canonical)}
          />
        )}
        <label className="knowledge-field">
          审核人
          <Input
            value={reviewer}
            maxLength={200}
            onChange={(event) => setReviewer(event.target.value)}
          />
        </label>
        <label className="knowledge-field">
          审核备注
          <Input.TextArea
            value={note}
            maxLength={4000}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
        {error && <ErrorNotice message={error} />}
      </Modal>
    </main>
  );
}

type BriefNode = Pick<KnowledgeNode, "id" | "name" | "type">;
function MergeFlow({
  nodes,
  target,
}: {
  nodes: BriefNode[];
  target?: BriefNode;
}) {
  return (
    <div
      className="merge-flow"
      aria-label={`${nodes.length} 个节点合并为 1 个节点`}
    >
      <div className="merge-flow-sources">
        {nodes.map((node) => (
          <div className="merge-node" key={node.id}>
            <strong>{node.name}</strong>
            <small>
              {node.type} · {node.id}
            </small>
          </div>
        ))}
      </div>
      <span aria-hidden="true">→</span>
      <div className="merge-node merge-node-target">
        <strong>{target?.name || "选择保留节点"}</strong>
        <small>
          {target ? `${target.type} · ${target.id}` : "合并后的唯一实体"}
        </small>
      </div>
    </div>
  );
}
function CandidateCard({
  candidate,
  disabled,
  onDecision,
}: {
  candidate: ResolutionCandidate;
  disabled: boolean;
  onDecision: (
    candidate: ResolutionCandidate,
    action: "merge" | "reject" | "reset",
    canonical?: string,
  ) => void;
}) {
  const [canonical, setCanonical] = useState(
    candidate.canonical_id || candidate.node_ids[0],
  );
  const target = candidate.nodes.find((node) => node.id === canonical);
  return (
    <article className="resolution-candidate">
      <div className="knowledge-toolbar">
        <h3>{candidate.nodes.map((node) => node.name).join(" / ")}</h3>
        <span>
          <StatusTag status={candidate.status} />
          候选得分 {candidate.score.toFixed(3)}
        </span>
      </div>
      <p className="hint">{candidate.reasons.join("；")}</p>
      <MergeFlow nodes={candidate.nodes} target={target} />
      {candidate.conflicts.length > 0 && (
        <>
          <Alert
            type="warning"
            showIcon
            title={`存在 ${candidate.conflicts.length} 项属性冲突，请核验`}
          />
          <Table
            size="small"
            rowKey="field"
            pagination={false}
            scroll={{ x: 400 }}
            dataSource={candidate.conflicts}
            columns={[
              { title: "冲突属性", dataIndex: "field" },
              {
                title: "各节点原值",
                render: (_, conflict) =>
                  conflict.values.map((item) => (
                    <div key={item.node_id}>
                      <strong>{item.node_id}：</strong>
                      <span>{pretty(item.value)}</span>
                    </div>
                  )),
              },
            ]}
          />
        </>
      )}
      <JsonDetails value={candidate.evidence} label="候选证据与来源" />
      {candidate.status === "pending" ? (
        <>
          <label className="knowledge-field">
            合并时保留的节点
            <Select
              aria-label={`保留节点 ${candidate.id}`}
              value={canonical}
              onChange={setCanonical}
              disabled={disabled}
              options={candidate.nodes.map((node) => ({
                value: node.id,
                label: `${node.name} · ${node.id}`,
              }))}
            />
          </label>
          <div className="knowledge-review-actions">
            <Button
              type="primary"
              disabled={disabled || !canonical}
              onClick={() => onDecision(candidate, "merge", canonical)}
            >
              确认合并
            </Button>
            <Button
              disabled={disabled}
              onClick={() => onDecision(candidate, "reject")}
            >
              保留为不同实体
            </Button>
          </div>
        </>
      ) : (
        <Button
          disabled={disabled}
          onClick={() => onDecision(candidate, "reset")}
        >
          撤销审核决定
        </Button>
      )}
    </article>
  );
}
