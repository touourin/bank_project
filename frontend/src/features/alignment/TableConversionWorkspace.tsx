import { useCallback, useEffect, useState } from "react";
import { Alert, Button } from "antd";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { isTableTaskRunning } from "../conversion/taskState";
import { useResource } from "../../hooks/useResource";
import { Panel } from "../../ui/Panel";
import { ErrorNotice, LoadingState } from "../../ui/Feedback";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { alignmentApi } from "./api";
import { GraphPanel } from "./GraphPanel";
import { GenerationRules } from "./GenerationRules";
import { GenerationPanel } from "./GenerationPanel";
import { RunOverview } from "./RunOverview";
import { generationPlan } from "./workflow";
import { GenerationConfirmation } from "./GenerationConfirmation";
import { MappingResults } from "./MappingResults";
import { SourcePicker } from "./SourcePicker";
import { useRun } from "./useRun";
import type { GraphTemplate, Selection } from "./types";
import type { GraphSourceRef } from "../knowledge/types";
import "./alignment.css";

export function TableConversionWorkspace({
  token,
  active,
  onAnalyze,
}: {
  token: string;
  active: boolean;
  onAnalyze: (source: GraphSourceRef) => void;
}) {
  const config = useResource(
    useCallback(
      (signal: AbortSignal) => alignmentApi.config(token, signal),
      [token],
    ),
    true,
  );
  const history = useResource(
    useCallback(
      (signal: AbortSignal) => alignmentApi.runs(token, signal),
      [token],
    ),
    true,
  );
  const graph = useResource(
    useCallback(
      (signal: AbortSignal) => alignmentApi.graph(token, signal),
      [token],
    ),
    true,
  );
  const [selections, setSelections] = useState<Selection[]>([]);
  const [runId, setRunId] = useState("");
  const [revision, setRevision] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const analysis = useAsyncAction();
  const error = analysis.error;
  const [confirm, setConfirm] = useState(false);
  const [templateDirty, setTemplateDirty] = useState(false);
  const [rulesSaving, setRulesSaving] = useState(false);
  const [draft, setDraft] = useState<{
    runId: string;
    template: GraphTemplate;
  }>();
  useEffect(() => setTemplateDirty(false), [runId]);
  const { run, error: runError } = useRun(token, runId, revision);
  const busy =
    submitting || rulesSaving || analysis.busy || isTableTaskRunning(run);
  const result = run?.result
    ? {
        ...run.result,
        template:
          draft?.runId === run.id ? draft.template : run.result.template,
      }
    : null;
  const plan = result ? generationPlan(result) : null;
  const refresh = () => {
    setRevision((value) => value + 1);
    history.refresh();
  };
  useEffect(() => {
    if (!runId && history.data?.[0]) setRunId(history.data[0].id);
  }, [history.data, runId]);
  const refreshGraph = graph.refresh;
  const refreshHistory = history.refresh;
  const refreshConfig = config.refresh;
  useEffect(() => {
    if (active && !templateDirty) {
      refreshHistory();
      refreshGraph();
      refreshConfig();
    }
  }, [active, templateDirty, refreshHistory, refreshGraph, refreshConfig]);
  useEffect(() => {
    if (run?.status === "ready" || run?.status === "failed") refreshHistory();
  }, [run?.id, run?.status, refreshHistory]);
  useEffect(() => {
    if (run?.graph_status === "ready") refreshGraph();
  }, [run?.id, run?.graph_status, refreshGraph]);

  async function analyze() {
    await analysis.execute(
      "analyze",
      () => alignmentApi.analyze(token, selections),
      {
        onSuccess: (created) => {
          setRunId(created.id);
          setRevision((value) => value + 1);
        },
        onSettled: history.refresh,
      },
    );
  }

  return (
    <section aria-label="表格转换工作区">
      {config.error ? (
        <ErrorNotice message={config.error} onRetry={config.refresh} />
      ) : !config.data ? (
        <LoadingState label="读取分析配置…" />
      ) : (
        <div className="alignment-layout">
          <div className="alignment-sidebar">
            <SourcePicker
              token={token}
              active={active}
              selections={selections}
              onChange={setSelections}
              disabled={Boolean(busy) || templateDirty}
            />
            <Panel title="开始分析" padded>
              <p className="hint">
                retrieve
                匹配表和字段的含义；本次分析只产出建议。大模型读取结构及最多 5
                行截取样例，确认后再分批生成图谱。
              </p>
              {config.data.catalog_error && (
                <ErrorNotice message={config.data.catalog_error} />
              )}
              {!config.data.model_configured && (
                <Alert type="warning" showIcon title="请先在后端配置大模型" />
              )}
              {config.data.retrieve_configured === false && (
                <Alert
                  type="warning"
                  showIcon
                  title="请先在后端配置 retrieve 接口"
                />
              )}
              <p className="hint">
                {config.data.ontology_source?.kind === "remote"
                  ? "远端本体"
                  : "本地本体"}
                ：{config.data.concept_count.toLocaleString()} 个概念
                <br />
                匹配分数阈值：{config.data.confidence_threshold.toFixed(3)}
                <br />
                匹配方式：
                {config.data.verification_mode === "external_retrieve"
                  ? "retrieve 接口匹配＋固定版本校验"
                  : "本地目录校验"}
              </p>
              <Button
                type="primary"
                block
                loading={analysis.busy}
                disabled={
                  !selections.length ||
                  busy ||
                  templateDirty ||
                  !config.data.model_configured ||
                  config.data.retrieve_configured === false ||
                  Boolean(config.data.catalog_error)
                }
                onClick={() => void analyze()}
              >
                分析所选 {selections.length} 张表
              </Button>
              {templateDirty && (
                <p className="hint">
                  可整体采纳方案并生成，或保存、撤销修改后切换任务。
                </p>
              )}
              {error && <ErrorNotice message={error} />}
            </Panel>
          </div>
          <div className="alignment-results">
            <RunOverview
              run={run ?? null}
              runId={runId}
              history={history.data ?? []}
              error={runError || history.error || ""}
              busy={Boolean(busy)}
              dirty={templateDirty}
              selections={selections}
              onSelect={setRunId}
              onRefresh={refresh}
            />
            {run?.result && (
              <MappingResults
                key={run.id}
                run={run}
                token={token}
                locked={templateDirty || rulesSaving}
                onSaved={(updated) => {
                  setRunId(updated.id);
                  history.refresh();
                }}
              />
            )}
            {run?.status === "ready" && run.result?.template && (
              <GenerationRules
                key={`template-${run.id}`}
                run={run}
                token={token}
                onDirty={setTemplateDirty}
                locked={Boolean(busy) || confirm}
                onDraft={(template) =>
                  setDraft(template ? { runId: run.id, template } : undefined)
                }
                onSaving={setRulesSaving}
                onSaved={(updated) => {
                  setRunId(updated.id);
                  history.refresh();
                }}
              />
            )}
            {run?.status === "ready" && run.result && (
              <GenerationPanel
                run={run}
                result={result!}
                dirty={templateDirty}
                busy={Boolean(busy)}
                configured={config.data.graph_configured}
                graph={graph.data}
                onGenerate={() => setConfirm(true)}
              />
            )}
            {graph.error ? (
              <ErrorNotice message={graph.error} onRetry={graph.refresh} />
            ) : graph.data ? (
              <GraphPanel
                key={graph.data.summary?.version ?? "empty"}
                token={token}
                graph={graph.data}
                selectedRunId={run?.id}
                onAnalyze={
                  graph.data.summary
                    ? () =>
                        onAnalyze({
                          kind: "database",
                          id: graph.data!.summary!.version,
                        })
                    : undefined
                }
                onRefresh={graph.refresh}
              />
            ) : (
              <LoadingState label="读取已发布图谱…" />
            )}
          </div>
        </div>
      )}
      {confirm && run && plan && (
        <ConfirmDialog
          title="采纳匹配方案并生成图谱"
          confirmLabel="采纳并开始生成"
          onClose={() => setConfirm(false)}
          onConfirm={async () => {
            setSubmitting(true);
            try {
              const updated = await alignmentApi.generate(
                token,
                run.id,
                result?.template ?? undefined,
              );
              setRunId(updated.id);
              setDraft(undefined);
              setTemplateDirty(false);
              refresh();
            } finally {
              setSubmitting(false);
            }
          }}
        >
          <GenerationConfirmation run={run} plan={plan} />
        </ConfirmDialog>
      )}
    </section>
  );
}
