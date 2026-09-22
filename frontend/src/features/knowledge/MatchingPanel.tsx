import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Select, Tabs } from "antd";
import { useResource } from "../../hooks/useResource";
import { useTaskResource } from "../../hooks/useTaskResource";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { isRunningTask, taskRevision } from "../conversion/taskState";
import {
  MatchReviewDialog,
  type MatchReviewTarget,
  type MatchReviewValues,
} from "./MatchReviewDialog";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { Panel } from "../../ui/Panel";
import { knowledgeApi } from "./api";
import { KnowledgeGraphPanel } from "./KnowledgeGraphPanel";
import { MatchResults, matchProposalCounts } from "./MatchResults";
import { AuditTable, StatusTag } from "./shared";
import type { MatchRun } from "./types";

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
  const resource = useTaskResource(
    useCallback(
      (signal: AbortSignal) =>
        runId
          ? knowledgeApi.match(token, runId, signal)
          : Promise.resolve(null),
      [token, runId],
    ),
    { isRunning: isRunningTask, revision: taskRevision },
  );
  const run = resource.data;
  const proposals = useMemo(
    () => (run ? matchProposalCounts(run) : { nodes: 0, edges: 0 }),
    [run],
  );
  const action = useAsyncAction();
  const { busy, execute } = action;
  const accepting = action.pending === "accept";
  const error = action.error;
  const [editing, setEditing] = useState<MatchReviewTarget>();
  const graph = useResource(
    useCallback(
      (signal: AbortSignal) =>
        run?.status === "ready"
          ? knowledgeApi.matchGraph(token, run.id, signal)
          : Promise.resolve(null),
      [token, run?.id, run?.status, run?.revision],
    ),
  );
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
  async function start() {
    await execute(
      "start",
      () => knowledgeApi.matchStart(token, selectedSource),
      {
        onSuccess: (created) => setRunId(created.id),
        onSettled: history.refresh,
      },
    );
  }
  function edit(target: MatchReviewTarget) {
    action.clearError();
    setEditing(target);
  }
  function reviewed(result: MatchRun) {
    resource.accept(result);
    resource.refresh();
    history.refresh();
    sources.refresh();
  }
  async function save({ selection, reviewer, note }: MatchReviewValues) {
    if (!run || !editing) return;
    try {
      reviewed(
        await knowledgeApi.matchDecision(token, run.id, {
          target: editing.target,
          target_id: editing.value.id,
          expected_revision: run.revision,
          ...(editing.target === "node"
            ? { boid: selection ?? null }
            : { edge_type: selection ?? null }),
          reviewer: reviewer.trim() || undefined,
          note,
        }),
      );
    } catch (reason) {
      resource.refresh();
      throw reason;
    }
  }
  async function acceptProposals() {
    if (!run) return;
    await execute(
      "accept",
      () =>
        knowledgeApi.matchAccept(token, run.id, {
          expected_revision: run.revision,
        }),
      {
        onSuccess: reviewed,
        onError: resource.refresh,
      },
    );
  }
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
      {editing && run && (
        <MatchReviewDialog
          key={`${run.id}:${editing.target}:${editing.value.id}`}
          token={token}
          runId={run.id}
          editing={editing}
          onSave={save}
          onClose={() => setEditing(undefined)}
        />
      )}
    </div>
  );
}
