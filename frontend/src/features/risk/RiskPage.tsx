import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Select } from "antd";
import { errorMessage } from "../../api/request";
import { useResource } from "../../hooks/useResource";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { Panel } from "../../ui/Panel";
import { riskApi } from "./api";
import { RiskCasePanel } from "./RiskCasePanel";
import { RiskJson, RiskStatus } from "./shared";
import type { PropagationJob, RiskNode } from "./types";
import "./risk.css";

export function RiskPage({
  token,
  active,
}: {
  token: string;
  active: boolean;
}) {
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [anchors, setAnchors] = useState<string[]>([]);
  const [selectedNodes, setSelectedNodes] = useState<RiskNode[]>([]);
  const [brief, setBrief] = useState("");
  const [job, setJob] = useState<PropagationJob>();
  const [caseId, setCaseId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [pollRevision, setPollRevision] = useState(0);
  const inFlight = useRef(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  const catalog = useResource(
    useCallback(
      (signal: AbortSignal) => riskApi.catalog(token, query, signal),
      [token, query],
    ),
    true,
  );
  const history = useResource(
    useCallback((signal: AbortSignal) => riskApi.jobs(token, signal), [token]),
    true,
  );
  const cases = useResource(
    useCallback((signal: AbortSignal) => riskApi.cases(token, signal), [token]),
    true,
  );
  const refreshCatalog = catalog.refresh;
  const refreshHistory = history.refresh;
  const refreshCases = cases.refresh;
  useEffect(() => {
    if (active) {
      refreshCatalog();
      refreshHistory();
      refreshCases();
      setPollRevision((value) => value + 1);
    }
  }, [active, refreshCatalog, refreshHistory, refreshCases]);
  useEffect(() => {
    if (!job && history.data?.[0]) setJob(history.data[0]);
  }, [job, history.data]);
  useEffect(() => {
    if (!caseId && cases.data?.[0]) setCaseId(cases.data[0].id);
  }, [caseId, cases.data]);
  useEffect(() => {
    if (!job || job.status === "running") return;
    const controller = new AbortController();
    setPollError("");
    riskApi
      .job(token, job.id, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) setJob(next);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setPollError(errorMessage(reason));
      });
    return () => controller.abort();
  }, [token, job?.id, pollRevision]);
  useEffect(() => {
    if (!job || job.status !== "running") return;
    const controller = new AbortController();
    let timer: number;
    setPollError("");
    const poll = async () => {
      try {
        const next = await riskApi.job(token, job.id, controller.signal);
        if (controller.signal.aborted) return;
        setPollError("");
        setJob(next);
        if (next.status !== "running") {
          history.refresh();
          cases.refresh();
          if (next.case_ids[0]) setCaseId(next.case_ids[0]);
        } else timer = window.setTimeout(poll, 1500);
      } catch (reason) {
        if (!controller.signal.aborted) setPollError(errorMessage(reason));
      }
    };
    timer = window.setTimeout(poll, 800);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [
    token,
    job?.id,
    job?.status,
    pollRevision,
    history.refresh,
    cases.refresh,
  ]);
  const options = [
    ...new Map(
      [...selectedNodes, ...(catalog.data?.nodes ?? [])].map((node) => [
        node.id,
        node,
      ]),
    ).values(),
  ];
  async function generate() {
    if (inFlight.current || !anchors.length || !brief.trim() || !catalog.data)
      return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const next = await riskApi.propagate(
        token,
        anchors,
        brief.trim(),
        catalog.data.revision,
      );
      setJob(next);
      history.refresh();
      if (next.status !== "running") cases.refresh();
    } catch (reason) {
      setError(errorMessage(reason));
      history.refresh();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return (
    <main className="risk-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">DATA WORKSPACE / STEP 05</p>
          <h1>风险规则</h1>
          <p className="description">
            从本体 WHY
            生成规则，核验依据和实例作用域，再查询已发布图谱中的交易。
          </p>
        </div>
        <span className="step-badge">
          <span>05</span> 规则 · 审核与执行
        </span>
      </div>
      <div className="risk-layout">
        <aside className="risk-sidebar">
          <Panel title="从本体生成规则" padded>
            {catalog.error && (
              <ErrorNotice message={catalog.error} onRetry={catalog.refresh} />
            )}
            {catalog.loading && !catalog.data && (
              <LoadingState label="正在读取本体与 WHY…" />
            )}
            {catalog.data?.source && (
              <p className="hint">
                {catalog.data.source.kind === "remote"
                  ? "远端本体"
                  : "本地本体"}
                {" · "}
                {catalog.data.source.node_count.toLocaleString()} 个概念
                {" · "}
                {catalog.data.source.why_node_count.toLocaleString()} 个含 WHY
              </p>
            )}
            <label className="risk-field">
              搜索 BO 节点
              <Input
                aria-label="搜索 BO 节点"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="按概念名称或编号搜索"
                allowClear
              />
            </label>
            <label className="risk-field">
              选择锚点
              <Select
                mode="multiple"
                maxCount={20}
                aria-label="选择风险锚点"
                value={anchors}
                loading={catalog.loading}
                placeholder="选择一个或多个 BO 节点"
                options={options.map((node) => ({
                  value: node.id,
                  label: `${node.name} · ${node.id}${node.semantic_type && node.semantic_type !== "bfo_concept" ? ` · ${node.semantic_type}` : ""}${node.has_why ? " · 有 WHY" : ""}`,
                }))}
                onChange={(value: string[]) => {
                  setAnchors(value);
                  setSelectedNodes(
                    options.filter((node) => value.includes(node.id)),
                  );
                }}
              />
            </label>
            {catalog.data && (
              <p className="hint">
                当前本体版本：{catalog.data.revision}
                。提交时固定完整快照，规则保留版本和哈希。
              </p>
            )}
            <label className="risk-field">
              分析需求
              <Input.TextArea
                aria-label="风险分析需求"
                value={brief}
                onChange={(event) => setBrief(event.target.value)}
                maxLength={4000}
                autoSize={{ minRows: 3, maxRows: 7 }}
                placeholder="说明希望识别的风险场景，参数仍须有 WHY 原文依据"
              />
            </label>
            <Button
              block
              type="primary"
              loading={busy}
              disabled={
                !anchors.length ||
                !brief.trim() ||
                !catalog.data ||
                catalog.loading ||
                Boolean(catalog.error) ||
                job?.status === "running"
              }
              onClick={() => void generate()}
            >
              生成待审核规则
            </Button>
            <p className="hint">
              沿本体关系最多查找 5 跳、50 个 WHY 来源。没有适用 WHY
              时不会补写规则。
            </p>
            {error && <ErrorNotice message={error} />}
          </Panel>
          <Panel
            title="生成任务"
            actions={
              <Button size="small" onClick={history.refresh}>
                刷新任务
              </Button>
            }
            padded
          >
            {history.error && (
              <ErrorNotice message={history.error} onRetry={history.refresh} />
            )}
            {history.loading && !history.data && <LoadingState />}
            <div className="risk-history">
              {history.data?.map((item) => (
                <button
                  key={item.id}
                  aria-pressed={item.id === job?.id}
                  onClick={() => {
                    if (item.id !== job?.id) setJob(item);
                    setPollError("");
                  }}
                >
                  <strong>
                    {item.created_at
                      ? new Date(item.created_at).toLocaleString()
                      : item.id}
                  </strong>
                  <small>
                    <RiskStatus
                      status={item.id === job?.id ? job.status : item.status}
                    />{" "}
                    {item.case_ids.length} 条规则
                  </small>
                </button>
              ))}
            </div>
            {history.data?.length === 0 && <EmptyState title="暂无生成任务" />}
          </Panel>
        </aside>
        <div className="risk-results">
          {job && (
            <Panel
              title="WHY 传导与生成进度"
              actions={<RiskStatus status={job.status} />}
              padded
            >
              <p role="status">{job.progress}</p>
              {job.status === "running" && (
                <LoadingState label="正在生成，任务状态会自动更新…" />
              )}
              {pollError && (
                <ErrorNotice
                  message={pollError}
                  onRetry={() => setPollRevision((value) => value + 1)}
                  retryLabel="恢复查询状态"
                />
              )}
              {job.error && <ErrorNotice message={job.error} />}
              {job.dataset_revision && (
                <p className="hint">固定本体版本：{job.dataset_revision}</p>
              )}
              {job.status === "succeeded" &&
                (job.case_ids.length ? (
                  <Alert
                    type="success"
                    showIcon
                    title={`已生成 ${job.case_ids.length} 条待审核规则`}
                  />
                ) : (
                  <Alert
                    type="info"
                    showIcon
                    title="未生成规则"
                    description="本次未获得可保存的规则候选。请检查锚点是否有可传导的 WHY 来源，以及下方覆盖与筛除记录。"
                  />
                ))}
              {job.coverage.complete === false && job.status !== "running" && (
                <Alert
                  type="warning"
                  showIcon
                  title="本次来源覆盖不完整"
                  description="请查看覆盖详情与筛除记录；结果不代表全部适用规则。"
                />
              )}
              <RiskJson
                label="固定快照、覆盖与筛除记录"
                value={{
                  snapshot_sha256: job.snapshot_sha256,
                  coverage: job.coverage,
                  anchors: job.anchors,
                  rejections: job.rejections,
                }}
              />
            </Panel>
          )}
          <Panel
            title="规则审核队列"
            actions={
              <Button size="small" onClick={cases.refresh}>
                刷新规则
              </Button>
            }
            padded
          >
            {cases.error && (
              <ErrorNotice message={cases.error} onRetry={cases.refresh} />
            )}
            {cases.loading && !cases.data && <LoadingState />}
            {!!cases.data?.length && (
              <Select
                aria-label="选择风险规则"
                className="risk-select"
                value={caseId || undefined}
                onChange={setCaseId}
                options={cases.data.map((item) => ({
                  value: item.id,
                  label: `${item.name} · ${item.review_status === "approved" ? "已批准" : item.review_status === "rejected" ? "已驳回" : "待审核"}`,
                }))}
              />
            )}
            {cases.data?.length === 0 && (
              <EmptyState
                title="暂无风险规则"
                description="选择 BO 节点并描述需求后生成候选。"
              />
            )}
          </Panel>
          {caseId && (
            <RiskCasePanel
              key={caseId}
              token={token}
              active={active}
              caseId={caseId}
              predicates={catalog.data?.predicates ?? []}
              onChanged={cases.refresh}
            />
          )}
        </div>
      </div>
    </main>
  );
}
