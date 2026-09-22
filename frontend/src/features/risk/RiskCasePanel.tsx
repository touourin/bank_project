import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Descriptions, Input, Tag } from "antd";
import { errorMessage } from "../../api/request";
import { useResource } from "../../hooks/useResource";
import { ErrorNotice, LoadingState } from "../../ui/Feedback";
import { DataTable } from "../../ui/DataTable";
import { Panel } from "../../ui/Panel";
import { riskApi } from "./api";
import { RiskExecutionPanel } from "./RiskExecutionPanel";
import { RiskJson, RiskStatus } from "./shared";
import type { RiskCase, RiskPredicate } from "./types";

export function RiskCasePanel({
  token,
  caseId,
  predicates,
  onChanged,
}: {
  token: string;
  caseId: string;
  predicates: RiskPredicate[];
  onChanged: () => void;
}) {
  const resource = useResource(
    useCallback(
      (signal: AbortSignal) => riskApi.case(token, caseId, signal),
      [token, caseId],
    ),
    true,
  );
  const [updated, setUpdated] = useState<RiskCase>();
  const value =
    updated && (!resource.data || updated.version > resource.data.version)
      ? updated
      : resource.data;
  const [confirmed, setConfirmed] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  useEffect(() => {
    setConfirmed(false);
    setReason("");
  }, [value?.version, value?.content_hash]);
  async function review(action: "approve" | "reject") {
    if (!value || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      setUpdated(
        await riskApi.review(token, value, action, confirmed, reason.trim()),
      );
      resource.refresh();
      onChanged();
    } catch (failure) {
      setError(errorMessage(failure));
      setUpdated(undefined);
      resource.refresh();
      onChanged();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  if (!value)
    return resource.error ? (
      <ErrorNotice message={resource.error} onRetry={resource.refresh} />
    ) : (
      <LoadingState label="正在加载规则与依据…" />
    );
  const binding = value.source_binding;
  const blocked = value.validation_issues.length > 0;
  const propagation = binding.propagation;
  return (
    <>
      <Panel
        title={value.name}
        description={value.description}
        actions={<RiskStatus status={value.review_status} />}
        padded
      >
        {resource.error && (
          <ErrorNotice message={resource.error} onRetry={resource.refresh} />
        )}
        <Descriptions
          column={1}
          size="small"
          items={[
            {
              key: "version",
              label: "规则版本",
              children: `${value.version} · ${value.execution_status === "ready" ? "允许执行" : "执行已阻断"}`,
            },
            {
              key: "revision",
              label: "固定本体版本",
              children: binding.dataset_revision,
            },
            {
              key: "anchor",
              label: "选定锚点",
              children: `${binding.anchor_node_name} · ${binding.anchor_node_id}`,
            },
            {
              key: "source",
              label: "WHY 来源",
              children: `${binding.source_node_name} · ${binding.source_node_id}`,
            },
            {
              key: "strength",
              label: "路径强度",
              children: String(propagation.path_strength),
            },
          ]}
        />
        <div className="risk-evidence">
          <h3>WHY 原文</h3>
          <pre className="risk-json">
            {typeof binding.why === "string"
              ? binding.why
              : JSON.stringify(binding.why, null, 2)}
          </pre>
        </div>
        <h3>规则模板与参数</h3>
        {value.rule_pack.body.map((body, index) => (
          <div className="risk-rule" key={index}>
            <strong>
              {predicates.find((item) => item.predicate === body.predicate)
                ?.name ?? body.predicate}
            </strong>
            <small>{body.predicate}</small>
            <pre className="risk-json">
              {JSON.stringify(body.params, null, 2)}
            </pre>
          </div>
        ))}
        <p className="hint">
          规则金额参数的 CNY_MINOR
          表示人民币分；执行时映射的原始交易金额字段必须以人民币元记录。命中表示符合规则模式，需结合业务证据判断。
        </p>
        <RiskJson
          label="参数与 WHY 引文对应关系"
          value={binding.parameter_sources}
        />
        <h3>传导路径</h3>
        {binding.propagation_path.length ? (
          <pre className="risk-json risk-path">
            {JSON.stringify(binding.propagation_path, null, 2)}
          </pre>
        ) : (
          <p className="hint">锚点自身的 WHY，无跨节点传导。</p>
        )}
        {(propagation.truncated_by_depth ||
          propagation.truncated_by_candidates) && (
          <Alert
            type="warning"
            showIcon
            title="WHY 来源搜索达到上限，候选覆盖不完整"
          />
        )}
        <h3>实例概念作用域（bo_scope）</h3>
        <p className="hint">
          服务端绑定为选定锚点及其 IS_A 子孙，共 {propagation.bo_scope.length}{" "}
          个概念；业务关系中的 WHY 来源不会自动加入实例范围。
        </p>
        <div className="risk-scope">
          {propagation.bo_scope.map((node) => (
            <Tag key={node}>{node}</Tag>
          ))}
        </div>
        <RiskJson
          label="内容、来源与作用域哈希"
          value={{
            rule_content_hash: value.content_hash,
            snapshot_sha256: binding.snapshot_sha256,
            why_dimension_hash: binding.dimension_hash,
            scope_hash: propagation.scope_hash,
          }}
        />
        <h3>人工审核</h3>
        {blocked && (
          <>
            <Alert
              type="warning"
              showIcon
              title="参数或来源依据不完整，不能批准或执行"
              description="请依据下面的问题补充本体 WHY 后重新生成，或驳回该候选。"
            />
            <DataTable
              label="规则校验问题"
              rowKey={(row) => `${row.field}:${row.reason}:${row.message}`}
              dataSource={value.validation_issues}
              columns={[
                { title: "字段", dataIndex: "field" },
                {
                  title: "问题",
                  dataIndex: "message",
                  render: (message: string | undefined, row) =>
                    message || row.reason,
                },
              ]}
            />
          </>
        )}
        <Checkbox
          checked={confirmed}
          disabled={busy || value.review_status === "approved" || blocked}
          onChange={(event) => setConfirmed(event.target.checked)}
        >
          我已核对 WHY 原文、参数引用、传导适用性及实例作用域
        </Checkbox>
        <label className="risk-field">
          审核意见
          <Input.TextArea
            aria-label="风险规则审核意见"
            value={reason}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
            maxLength={2000}
            placeholder="批准时选填；驳回时填写原因"
            autoSize={{ minRows: 2, maxRows: 5 }}
          />
        </label>
        <div className="risk-actions">
          <Button
            type="primary"
            loading={busy}
            disabled={
              !confirmed ||
              blocked ||
              value.review_status === "approved" ||
              resource.loading ||
              Boolean(resource.error)
            }
            onClick={() => void review("approve")}
          >
            批准规则
          </Button>
          <Button
            danger
            disabled={
              busy ||
              !reason.trim() ||
              value.review_status === "rejected" ||
              resource.loading ||
              Boolean(resource.error)
            }
            onClick={() => void review("reject")}
          >
            驳回规则
          </Button>
        </div>
        {error && <ErrorNotice message={error} />}
        <RiskJson
          label={`审核记录（${value.audits.length}）`}
          value={value.audits}
        />
      </Panel>
      <RiskExecutionPanel
        key={`${value.id}:${value.version}`}
        token={token}
        value={value}
        predicates={predicates}
        onStale={resource.refresh}
      />
    </>
  );
}
