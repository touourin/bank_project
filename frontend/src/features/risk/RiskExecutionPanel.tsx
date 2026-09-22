import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Select } from "antd";
import { errorMessage } from "../../api/request";
import { useResource } from "../../hooks/useResource";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { DataTable } from "../../ui/DataTable";
import { Panel } from "../../ui/Panel";
import { riskApi } from "./api";
import { fieldLabels, RiskJson, RiskStatus } from "./shared";
import type { RiskCase, RiskExecution, RiskPredicate } from "./types";

function validWindow(start: string, end: string) {
  const zoned =
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/;
  const duration = Date.parse(end) - Date.parse(start);
  return (
    zoned.test(start) &&
    zoned.test(end) &&
    duration > 0 &&
    duration <= 366 * 86400000
  );
}

export function RiskExecutionPanel({
  token,
  active,
  value,
  predicates,
  onStale,
}: {
  token: string;
  active: boolean;
  value: RiskCase;
  predicates: RiskPredicate[];
  onStale: () => void;
}) {
  const approved =
    value.review_status === "approved" &&
    value.execution_status === "ready" &&
    !value.validation_issues.length;
  const sources = useResource(
    useCallback(
      (signal: AbortSignal) =>
        approved ? riskApi.sources(token, signal) : Promise.resolve([]),
      [token, approved],
    ),
  );
  const [source, setSource] = useState("");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [latest, setLatest] = useState<RiskExecution>();
  const inFlight = useRef(false);
  const fields = useResource(
    useCallback(
      (signal: AbortSignal) =>
        approved && source
          ? riskApi.fields(token, value.id, source, signal)
          : Promise.resolve(null),
      [token, approved, source, value.id],
    ),
  );
  const history = useResource(
    useCallback(
      (signal: AbortSignal) => riskApi.executions(token, value.id, signal),
      [token, value.id],
    ),
    true,
  );
  const refreshSources = sources.refresh;
  const refreshHistory = history.refresh;
  useEffect(() => {
    if (active) {
      refreshSources();
      refreshHistory();
    }
  }, [active, refreshSources, refreshHistory]);
  const body = value.rule_pack.body[0];
  const predicate = predicates.find(
    (item) => item.predicate === body?.predicate,
  );
  const required = new Set(predicate?.required_fields ?? []);
  if (body?.params.transaction_type != null || body?.params.txn_type != null)
    required.add("txn_type");
  const canonical = [
    ...new Set([...required, ...(predicate?.optional_fields ?? [])]),
  ];
  const complete =
    Boolean(predicate) &&
    [...required].every((field) => mapping[field]) &&
    new Set(Object.values(mapping)).size === Object.keys(mapping).length;
  const windowValid = validWindow(start.trim(), end.trim());
  const execution = latest
    ? (history.data?.find(
        (item) => item.id === latest.id && item.status !== "running",
      ) ?? latest)
    : history.data?.[0];
  async function execute() {
    if (inFlight.current || !approved || !complete || !windowValid || !source)
      return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      setLatest(
        await riskApi.execute(token, value, {
          graph_version: source,
          field_mapping: mapping,
          start: start.trim(),
          end: end.trim(),
        }),
      );
      history.refresh();
    } catch (failure) {
      setError(errorMessage(failure));
      history.refresh();
      onStale();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return (
    <Panel
      title="查询交易实例"
      description="选择已发布数据库图谱，明确字段含义和时间窗口后执行。"
      actions={
        approved && (
          <Button
            size="small"
            disabled={busy}
            loading={sources.loading}
            onClick={refreshSources}
          >
            刷新图谱列表
          </Button>
        )
      }
      padded
    >
      {!approved && (
        <Alert
          type="info"
          showIcon
          title="审核通过后开放执行"
          description="当前规则处于阻断状态，请先核验来源依据并完成审核。"
        />
      )}
      {approved && (
        <>
          {sources.error && (
            <ErrorNotice message={sources.error} onRetry={sources.refresh} />
          )}
          <label className="risk-field">
            已发布数据库图谱
            <Select
              aria-label="风险执行图谱"
              value={source || undefined}
              placeholder="选择 graph_version"
              loading={sources.loading}
              disabled={busy}
              options={sources.data?.map((item) => ({
                value: item.id,
                label: `${item.name} · ${item.id}`,
              }))}
              onChange={(next) => {
                setSource(next);
                setMapping({});
                setError("");
              }}
            />
          </label>
          {sources.data?.length === 0 && (
            <EmptyState
              title="暂无已发布数据库图谱"
              description="请在第二步完成本体对齐并发布实例图谱。"
            />
          )}
          {fields.error && (
            <ErrorNotice message={fields.error} onRetry={fields.refresh} />
          )}
          {fields.loading && source && (
            <LoadingState label="正在读取作用域内原始字段…" />
          )}
          {fields.data && (
            <>
              <p className="hint">
                作用域包含 {fields.data.total_count} 个实例，字段样本来自{" "}
                {fields.data.sampled_count} 个实例
                {fields.data.truncated ? "（字段列表为抽样结果）" : ""}
                。请逐项选择原始字段，金额按人民币元、交易时间按带时区 ISO8601
                读取。
              </p>
              {!predicate && (
                <Alert
                  type="warning"
                  showIcon
                  title="尚未获取该规则的字段契约"
                  description="请刷新本体目录，取得服务端支持的谓词字段后再执行。"
                />
              )}
              <div className="risk-field-grid">
                {canonical.map((field) => (
                  <label key={field} className="risk-field">
                    {fieldLabels[field] ?? field}{" "}
                    {required.has(field) ? "· 必填" : "· 可选"}
                    <Select
                      aria-label={`映射 ${field}`}
                      value={mapping[field]}
                      disabled={busy}
                      allowClear
                      placeholder="明确选择原始字段"
                      options={fields.data?.fields.map((item) => ({
                        value: item.name,
                        label: `${item.name} · ${item.present_count} 个非空样本`,
                        disabled: Object.entries(mapping).some(
                          ([key, mapped]) =>
                            key !== field && mapped === item.name,
                        ),
                      }))}
                      onChange={(next?: string) =>
                        setMapping((current) => {
                          const updated = { ...current };
                          if (next) updated[field] = next;
                          else delete updated[field];
                          return updated;
                        })
                      }
                    />
                    {mapping[field] && (
                      <small className="risk-samples">
                        样本：
                        {fields.data?.fields
                          .find((item) => item.name === mapping[field])
                          ?.samples.map((sample) =>
                            typeof sample === "string"
                              ? sample
                              : JSON.stringify(sample),
                          )
                          .join(" / ") || "暂无非空样本"}
                      </small>
                    )}
                  </label>
                ))}
              </div>
            </>
          )}
          <div className="risk-field-grid">
            <label className="risk-field">
              开始时间（包含）
              <Input
                aria-label="风险开始时间"
                maxLength={64}
                value={start}
                disabled={busy}
                onChange={(event) => setStart(event.target.value)}
                placeholder="2026-09-01T00:00:00+08:00"
              />
            </label>
            <label className="risk-field">
              结束时间（不包含）
              <Input
                aria-label="风险结束时间"
                maxLength={64}
                value={end}
                disabled={busy}
                onChange={(event) => setEnd(event.target.value)}
                placeholder="2026-09-02T00:00:00+08:00"
              />
            </label>
          </div>
          <p className="hint">
            起止时间须明确包含 Z 或 +08:00 等时区，范围为正且不超过 366
            天。现金累计规则按 +08:00 自然日统计。
          </p>
          {!!start && !!end && !windowValid && (
            <Alert type="warning" showIcon title="请填写有效的带时区时间范围" />
          )}
          <Button
            type="primary"
            loading={busy}
            disabled={
              !source ||
              !fields.data ||
              fields.loading ||
              Boolean(fields.error) ||
              !complete ||
              !windowValid
            }
            onClick={() => void execute()}
          >
            执行已批准规则
          </Button>
          {error && <ErrorNotice message={error} />}
        </>
      )}
      <div className="risk-actions">
        <h3>执行记录</h3>
        <Button size="small" onClick={history.refresh}>
          刷新执行记录
        </Button>
      </div>
      {history.error && (
        <ErrorNotice message={history.error} onRetry={history.refresh} />
      )}
      {!!history.data?.length && (
        <Select
          aria-label="风险执行记录"
          className="risk-select"
          value={execution?.id}
          onChange={(key) =>
            setLatest(history.data?.find((item) => item.id === key))
          }
          options={history.data.map((item) => ({
            value: item.id,
            label: `${new Date(item.created_at).toLocaleString()} · ${item.status === "succeeded" ? "已完成" : item.status === "failed" ? "失败" : "执行中"}`,
          }))}
        />
      )}
      {!execution && !history.loading && <EmptyState title="暂无执行记录" />}
      {execution && <ExecutionResult execution={execution} />}
    </Panel>
  );
}

function ExecutionResult({ execution }: { execution: RiskExecution }) {
  const result = execution.result;
  return (
    <div className="risk-execution-result">
      <div className="risk-actions">
        <RiskStatus status={execution.status} runningLabel="执行中" />
        <span className="hint">图谱版本：{execution.graph_version}</span>
      </div>
      <p className="hint">
        {execution.start} → {execution.end}
      </p>
      {execution.error && <ErrorNotice message={execution.error} />}
      {result && (
        <>
          <div className="risk-metrics">
            <div>
              <span>扫描实例</span>
              <strong>{result.counts.scanned}</strong>
            </div>
            <div>
              <span>窗口内交易</span>
              <strong>{result.counts.within_window}</strong>
            </div>
            <div>
              <span>匹配交易</span>
              <strong>{result.counts.matched_transactions}</strong>
            </div>
            <div>
              <span>风险命中</span>
              <strong>{result.counts.hit_count}</strong>
            </div>
          </div>
          {(result.truncation.hits ||
            result.truncation.evidence ||
            result.truncation.scope) && (
            <Alert
              type="warning"
              showIcon
              title="展示结果已达到上限"
              description="命中或交易证据有截断，请查看查询详情中的上限与截断标记。"
            />
          )}
          {result.hits.length ? (
            <DataTable
              label="风险命中结果"
              rowKey="id"
              dataSource={result.hits}
              columns={[
                { title: "账户", dataIndex: "account_id" },
                {
                  title: "统计日",
                  dataIndex: "day",
                  render: (day?: string) => day || "整个窗口",
                },
                { title: "交易笔数", dataIndex: "count" },
                {
                  title: "累计金额",
                  dataIndex: "total",
                  render: (money: { value: string | number; unit: string }) =>
                    `${money.value} ${money.unit === "CNY_MINOR" ? "分（人民币）" : money.unit}`,
                },
              ]}
              expandable={{
                expandedRowRender: (hit) => (
                  <pre className="risk-json">
                    {JSON.stringify(hit.transactions, null, 2)}
                  </pre>
                ),
                columnTitle: "交易证据",
                columnWidth: 90,
              }}
            />
          ) : (
            <EmptyState title="当前范围未命中规则" />
          )}
          <RiskJson label="完整查询结果、映射与截断信息" value={execution} />
        </>
      )}
    </div>
  );
}
