import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button } from "antd";
import { errorMessage } from "../../api/request";
import { useResource } from "../../hooks/useResource";
import { Panel } from "../../ui/Panel";
import { ErrorNotice, LoadingState } from "../../ui/Feedback";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { alignmentApi } from "./api";
import { GraphPanel } from "./GraphPanel";
import { GenerationRules } from "./GenerationRules";
import { GenerationPanel } from "./GenerationPanel";
import { RunOverview } from "./RunOverview";
import { generationPlan, tableLabel } from "./workflow";
import { MappingResults } from "./MappingResults";
import { SourcePicker } from "./SourcePicker";
import { useRun } from "./useRun";
import type { GraphTemplate, Selection } from "./types";
import "./alignment.css";

export function AlignmentPage({ token }: { token: string }) {
  const config = useResource(
    useCallback(
      (signal: AbortSignal) => alignmentApi.config(token, signal),
      [token],
    ),
  );
  const history = useResource(
    useCallback(
      (signal: AbortSignal) => alignmentApi.runs(token, signal),
      [token],
    ),
  );
  const graph = useResource(
    useCallback(
      (signal: AbortSignal) => alignmentApi.graph(token, signal),
      [token],
    ),
  );
  const [selections, setSelections] = useState<Selection[]>([]);
  const [runId, setRunId] = useState("");
  const [revision, setRevision] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [templateDirty, setTemplateDirty] = useState(false);
  const [rulesSaving, setRulesSaving] = useState(false);
  const [draft, setDraft] = useState<{
    runId: string;
    template: GraphTemplate;
  }>();
  useEffect(() => setTemplateDirty(false), [runId]);
  const inFlight = useRef(false);
  const { run, error: runError } = useRun(token, runId, revision);
  const busy =
    submitting ||
    rulesSaving ||
    run?.status === "analyzing" ||
    run?.graph_status === "building";
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
  useEffect(() => {
    if (run?.status === "ready" || run?.status === "failed") refreshHistory();
  }, [run?.id, run?.status, refreshHistory]);
  useEffect(() => {
    if (run?.graph_status === "ready") refreshGraph();
  }, [run?.id, run?.graph_status, refreshGraph]);

  async function analyze() {
    if (inFlight.current) return;
    inFlight.current = true;
    setSubmitting(true);
    setError("");
    try {
      const created = await alignmentApi.analyze(token, selections);
      setRunId(created.id);
      setRevision((value) => value + 1);
      history.refresh();
    } catch (reason) {
      setError(errorMessage(reason));
      history.refresh();
    } finally {
      inFlight.current = false;
      setSubmitting(false);
    }
  }

  return (
    <main className="alignment-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">DATA WORKSPACE / STEP 02</p>
          <h1>本体对齐与图谱生成</h1>
          <p className="description">
            把数据表变成业务对象：自动给出匹配方案，整体采纳后生成可追溯的图谱。
          </p>
        </div>
        <span className="step-badge">
          <span>02</span> 第二步 · 匹配与构图
        </span>
      </div>
      <ol className="alignment-steps" aria-label="第二步操作顺序">
        <li>
          <span>1</span>
          <div>
            <strong>选择数据</strong>
            <small>勾选表，发起新分析</small>
          </div>
        </li>
        <li>
          <span>2</span>
          <div>
            <strong>查看匹配方案</strong>
            <small>自动预填，按需修改</small>
          </div>
        </li>
        <li>
          <span>3</span>
          <div>
            <strong>生成并查看结果</strong>
            <small>查看实例、关系及来源</small>
          </div>
        </li>
      </ol>
      {config.error ? (
        <ErrorNotice message={config.error} onRetry={config.refresh} />
      ) : !config.data ? (
        <LoadingState label="读取分析配置…" />
      ) : (
        <div className="alignment-layout">
          <div className="alignment-sidebar">
            <SourcePicker
              token={token}
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
                本地本体：{config.data.concept_count.toLocaleString()} 个概念
                <br />
                匹配分数阈值：{config.data.confidence_threshold.toFixed(3)}
                <br />
                匹配方式：
                {config.data.verification_mode === "external_retrieve"
                  ? "retrieve 接口匹配＋本地版本校验"
                  : "本地目录校验"}
              </p>
              <Button
                type="primary"
                block
                loading={submitting}
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
          <p>
            任务 {run.id.slice(0, 8)} · 本次采用 {plan.included.length} 张表，共{" "}
            {plan.rows.toLocaleString()} 行来源数据。
          </p>
          <ul>
            {plan.included.map((table) => (
              <li key={table.table_id}>
                {tableLabel(table)} · {table.row_count.toLocaleString()} 行
              </li>
            ))}
          </ul>
          <p>
            对象类型：{plan.concepts.join("、")}。关系规则：{plan.relationRules}{" "}
            条。
          </p>
          <p>
            {plan.suggestedNodes > 0 &&
              `包含 ${plan.suggestedNodes} 个低分或未验证的对象建议；采纳后仍保留原始得分与依据。`}
            普通字段未匹配时保留原始属性，无需逐项确认。
          </p>
          <p>
            实际节点与关系数量在生成后统计。
            {plan.relationRules === 0 && "当前未配置关系，将只生成节点。"}
          </p>
          {plan.excluded.length > 0 && (
            <p>不参与生成：{plan.excluded.map(tableLabel).join("、")}。</p>
          )}
          <p>
            按确认的规则识别对象并检查属性冲突。新图生成成功后切换当前版本，旧版本保留。
          </p>
        </ConfirmDialog>
      )}
    </main>
  );
}
