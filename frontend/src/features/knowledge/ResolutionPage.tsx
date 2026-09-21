import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Input,
  Modal,
  Pagination,
  Progress,
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
import { AuditTable, JsonDetails, StatusTag, usePolling } from "./shared";
import type {
  KnowledgeNode,
  ResolutionCandidate,
  ResolutionRun,
  SourceKind,
} from "./types";
import {
  ResolutionOptionsPanel,
  defaultResolutionOptions,
} from "./ResolutionOptionsPanel";
import { ResolutionSourcesPanel } from "./ResolutionSources";
import { ResolutionDifferences } from "./ResolutionDifferences";
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
  const [options, setOptions] = useState(defaultResolutionOptions);
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
  const [filter, setFilter] = useState("all");
  const [verdictFilter, setVerdictFilter] = useState("all");
  const [candidatePage, setCandidatePage] = useState(1);
  const [suggestionPage, setSuggestionPage] = useState(1);
  useEffect(() => setSuggestionPage(1), [runId]);
  useEffect(() => setCandidatePage(1), [filter, verdictFilter, runId]);
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
        setFilter("all");
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
        () => knowledgeApi.resolve(token, selected.kind, selected.id, options),
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
  const suggestions = run?.candidates.filter(isMergeSuggestion) ?? [];
  const suggestionCurrentPage = Math.min(
    suggestionPage,
    Math.max(1, Math.ceil(suggestions.length / 10)),
  );
  const candidates =
    run?.candidates.filter((candidate) => {
      const verdict = effectiveVerdict(candidate);
      const guard = identityGuard(candidate);
      return (
        (filter === "all" || candidate.status === filter) &&
        (verdictFilter === "all" ||
          (verdictFilter === "same" && verdict === "same") ||
          (verdictFilter === "different" && verdict === "different") ||
          (verdictFilter === "uncertain" &&
            verdict === "uncertain" &&
            (guard || candidate.evidence.origin !== "error")) ||
          (verdictFilter === "error" &&
            !guard &&
            candidate.evidence.origin === "error"))
      );
    }) ?? [];
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
            <ResolutionOptionsPanel
              value={options}
              onChange={setOptions}
              onError={setError}
            />
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
              先由规则和大模型分析。只把模型建议合并且通过身份校验的记录提交确认；其他结果保留在分析记录中。
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
                  <>
                    <LoadingState label="正在分析，已返回的候选可提前查看…" />
                    {run.diagnostics.analysis && (
                      <>
                        <Progress
                          percent={
                            run.diagnostics.analysis.total
                              ? Math.floor(
                                  (100 * run.diagnostics.analysis.completed) /
                                    run.diagnostics.analysis.total,
                                )
                              : 0
                          }
                        />
                        <Alert
                          type="info"
                          showIcon
                          title={`分析中预览：已处理 ${run.diagnostics.analysis.completed}/${run.diagnostics.analysis.total} 对，仅预览模型合并建议。最多保存 ${run.diagnostics.analysis.preview_limit} 条预览记录，分析完成后开放确认。`}
                        />
                      </>
                    )}
                  </>
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
                    <span>模型建议 · 待确认</span>
                    <strong>{run.summary.pending_count}</strong>
                  </div>
                  <div>
                    <span>自动不合并</span>
                    <strong>{run.summary.excluded_count ?? 0}</strong>
                  </div>
                  <div>
                    <span>未形成合并建议</span>
                    <strong>{run.summary.not_recommended_count ?? 0}</strong>
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
                  key={run.id}
                  items={[
                    {
                      key: "candidates",
                      label: `${run.status === "analyzing" ? "建议预览" : "合并建议"}（${run.summary.pending_count}）`,
                      children: (
                        <>
                          <p className="hint">
                            这里只展示大模型建议合并并通过身份与来源校验的节点对。查看模型理由和引用来源后确认。
                          </p>
                          {suggestions.length ? (
                            suggestions
                              .slice(
                                (suggestionCurrentPage - 1) * 10,
                                suggestionCurrentPage * 10,
                              )
                              .map((candidate) => (
                                <CandidateCard
                                  key={`${run.id}:${candidate.id}:${candidate.status}`}
                                  candidate={candidate}
                                  token={token}
                                  runId={run.id}
                                  sourceKind={run.source_kind}
                                  sourcesAvailable={run.status === "ready"}
                                  disabled={busy || run.status !== "ready"}
                                  onDecision={decision}
                                />
                              ))
                          ) : (
                            <EmptyState
                              title={
                                run.status === "analyzing"
                                  ? "等待大模型返回合并建议"
                                  : "暂无大模型建议合并的节点"
                              }
                              description="节点均保持独立；其他判定与失败原因可在“分析记录”查看。"
                            />
                          )}
                          {suggestions.length > 10 && (
                            <Pagination
                              current={suggestionCurrentPage}
                              pageSize={10}
                              total={suggestions.length}
                              onChange={setSuggestionPage}
                              showSizeChanger={false}
                              showTotal={(total) => `共 ${total} 组合并建议`}
                            />
                          )}
                        </>
                      ),
                    },
                    {
                      key: "analysis",
                      label: `分析记录（${run.candidates.length}）`,
                      children: (
                        <>
                          <div className="knowledge-toolbar">
                            <Select
                              aria-label="候选状态"
                              value={filter}
                              onChange={setFilter}
                              options={[
                                {
                                  value: "pending",
                                  label: "模型建议 · 待确认",
                                },
                                {
                                  value: "not_recommended",
                                  label: "未建议合并",
                                },
                                { value: "excluded", label: "自动不合并" },
                                { value: "merged", label: "已合并" },
                                { value: "rejected", label: "已保留" },
                                { value: "all", label: "全部分析记录" },
                              ]}
                            />
                            <Select
                              aria-label="模型判定筛选"
                              value={verdictFilter}
                              onChange={setVerdictFilter}
                              options={[
                                { value: "all", label: "全部判定" },
                                { value: "same", label: "建议同一实体" },
                                { value: "different", label: "判为不同实体" },
                                { value: "uncertain", label: "证据不足" },
                                { value: "error", label: "失败或预算未覆盖" },
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
                                  token={token}
                                  runId={run.id}
                                  sourceKind={run.source_kind}
                                  sourcesAvailable={run.status === "ready"}
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
type IdentityGuard = {
  block_merge: boolean;
  verdict: string;
  message: string;
  differences: { field: string; left: unknown[]; right: unknown[] }[];
};
function identityGuard(
  candidate: ResolutionCandidate,
): IdentityGuard | undefined {
  const value = candidate.evidence.identity_guard;
  return value && typeof value === "object"
    ? (value as IdentityGuard)
    : undefined;
}
function effectiveVerdict(candidate: ResolutionCandidate) {
  return (
    candidate.evidence.effective_verdict ??
    candidate.evidence.proposal ??
    candidate.evidence.verdict ??
    "uncertain"
  );
}
function isMergeSuggestion(candidate: ResolutionCandidate) {
  return (
    candidate.status === "pending" &&
    candidate.evidence.origin === "model" &&
    effectiveVerdict(candidate) === "same" &&
    !identityGuard(candidate)?.block_merge &&
    quoteValidation(candidate)?.supported !== false &&
    typeof candidate.evidence.left_quote === "string" &&
    Boolean(candidate.evidence.left_quote.trim()) &&
    typeof candidate.evidence.right_quote === "string" &&
    Boolean(candidate.evidence.right_quote.trim())
  );
}

type QuoteLocation = {
  origin: "source_text" | "source_record" | "node_attribute" | "unverified";
  message: string;
  fields?: string[];
  excerpt?: string;
  text_unit_ids?: string[];
};
function quoteValidation(candidate: ResolutionCandidate) {
  return candidate.evidence.quote_validation as
    | { supported: boolean; left: QuoteLocation; right: QuoteLocation }
    | undefined;
}
function quoteDetails(quote: unknown, location?: QuoteLocation) {
  const labels = {
    source_text: "文档原文",
    source_record: "数据库来源字段",
    node_attribute: "GraphRAG 抽取后的节点属性（不是文档原文）",
    unverified: "来源未核实",
  };
  return {
    引用文字: quote,
    引用来源: location ? labels[location.origin] : "历史引用，尚无来源定位信息",
    核验结果: location?.message,
    属性字段: location?.fields,
    原文上下文: location?.excerpt,
    原文分块ID: location?.text_unit_ids,
  };
}

function CandidateCard({
  candidate,
  token,
  runId,
  sourceKind,
  sourcesAvailable,
  disabled,
  onDecision,
}: {
  candidate: ResolutionCandidate;
  token: string;
  runId: string;
  sourceKind: SourceKind;
  sourcesAvailable: boolean;
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
  const guard = identityGuard(candidate);
  const blocked = Boolean(guard?.block_merge);
  const verdict = effectiveVerdict(candidate);
  const excluded = candidate.status === "excluded";
  const incompleteIdentity = guard?.verdict === "uncertain";
  const recommended = isMergeSuggestion(candidate);
  const notRecommended = candidate.status === "not_recommended";
  const validation = quoteValidation(candidate);
  const label = excluded
    ? incompleteIdentity
      ? "身份依据不足，暂不合并，保留独立节点"
      : "已自动判定不合并，保留独立节点"
    : (guard?.message ??
      (candidate.status === "merged"
        ? "已确认合并"
        : candidate.status === "rejected"
          ? "已人工保留为不同实体"
          : verdict === "same"
            ? recommended
              ? candidate.evidence.simulated
                ? "模拟合并建议（演示），等待确认"
                : "大模型建议合并，等待确认"
              : "未形成有效的大模型合并建议"
            : verdict === "different"
              ? "判定为不同实体，尚未合并"
              : candidate.evidence.origin === "error"
                ? "判断失败，尚未确认身份"
                : "证据不足，尚未确认身份"));
  return (
    <article className="resolution-candidate">
      <div className="knowledge-toolbar">
        <h3>{candidate.nodes.map((node) => node.name).join(" / ")}</h3>
        <span>
          <StatusTag status={candidate.status} />
          名称相似度 {candidate.score.toFixed(3)}
        </span>
      </div>
      <p className="hint">{candidate.reasons.join("；")}</p>
      <Alert
        type={
          excluded
            ? "info"
            : blocked
              ? "error"
              : guard || candidate.evidence.origin === "error"
                ? "warning"
                : "info"
        }
        showIcon
        title={label}
        description={
          recommended
            ? String(
                candidate.evidence.model_reason ||
                  candidate.evidence.reason ||
                  "",
              )
            : undefined
        }
      />
      {candidate.evidence.origin === "model" &&
        Boolean(
          candidate.evidence.left_quote || candidate.evidence.right_quote,
        ) && (
          <JsonDetails
            label="大模型引用与来源"
            value={{
              左侧引用: quoteDetails(
                candidate.evidence.left_quote,
                validation?.left,
              ),
              右侧引用: quoteDetails(
                candidate.evidence.right_quote,
                validation?.right,
              ),
            }}
          />
        )}
      {validation?.supported === false && (
        <Alert
          showIcon
          type="warning"
          title="模型引用未通过来源校验"
          description="引用可能仅存在于抽取后的名称或描述中，不能冒充文档原文；该结果不进入合并建议。"
        />
      )}
      {candidate.status === "merged" ? (
        <MergeFlow nodes={candidate.nodes} target={target} />
      ) : (
        <div
          className="resolution-pair"
          aria-label={excluded ? "已保留的独立实体" : "待核验实体对，尚未合并"}
        >
          {candidate.nodes.map((node) => (
            <div className="merge-node" key={node.id}>
              <strong>{node.name}</strong>
              <small>
                {node.type} · {node.id}
              </small>
            </div>
          ))}
        </div>
      )}
      <ResolutionSourcesPanel
        token={token}
        runId={runId}
        candidateId={candidate.id}
        available={sourcesAvailable}
      />
      {guard && (
        <Table
          size="small"
          rowKey="field"
          pagination={false}
          scroll={{ x: 400 }}
          dataSource={guard.differences}
          columns={[
            { title: "身份限定", dataIndex: "field" },
            {
              title: "左侧记录",
              dataIndex: "left",
              render: (values: unknown[]) =>
                values.length ? values.join("、") : "未明确",
            },
            {
              title: "右侧记录",
              dataIndex: "right",
              render: (values: unknown[]) =>
                values.length ? values.join("、") : "未明确",
            },
          ]}
        />
      )}

      <ResolutionDifferences candidate={candidate} sourceKind={sourceKind} />
      <JsonDetails value={candidate.evidence} label="候选证据与来源" />
      {excluded ? (
        <p className="hint">
          {incompleteIdentity
            ? "身份限定未对齐，当前保留为独立节点；补全来源信息后可重新分析。"
            : "身份字段明确冲突，已保留为不同实体，无需人工审核。"}
        </p>
      ) : notRecommended ? (
        <p className="hint">
          未形成有效的大模型合并建议，节点保持独立，不进入确认队列。
        </p>
      ) : candidate.status === "pending" ? (
        <>
          <label className="knowledge-field">
            合并时保留的节点
            <Select
              aria-label={`保留节点 ${candidate.id}`}
              value={canonical}
              onChange={setCanonical}
              disabled={disabled || blocked}
              options={candidate.nodes.map((node) => ({
                value: node.id,
                label: `${node.name} · ${node.id}`,
              }))}
            />
          </label>
          <div className="knowledge-review-actions">
            <Button
              type="primary"
              disabled={disabled || blocked || !canonical}
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
