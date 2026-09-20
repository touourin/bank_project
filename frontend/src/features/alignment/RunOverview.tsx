import { Alert, Button, Select, Tag } from "antd";
import { Panel } from "../../ui/Panel";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { sameSources, taskStatus } from "./workflow";
import type { Run, Selection } from "./types";

export function RunOverview({
  run,
  runId,
  history,
  error,
  busy,
  dirty,
  selections,
  onSelect,
  onRefresh,
}: {
  run: Run | null;
  runId: string;
  history: Run[];
  error: string;
  busy: boolean;
  dirty: boolean;
  selections: Selection[];
  onSelect: (id: string) => void;
  onRefresh: () => void;
}) {
  const tasks =
    run && !history.some((item) => item.id === run.id)
      ? [run, ...history]
      : history;
  return (
    <Panel
      title="当前分析任务"
      description="下方匹配和生成规则属于这个任务。左侧勾选用于发起新分析。"
      padded
      actions={
        <Button size="small" disabled={dirty} onClick={onRefresh}>
          刷新任务
        </Button>
      }
    >
      {tasks.length > 0 && (
        <Select
          className="run-select"
          aria-label="选择分析任务"
          value={runId || undefined}
          disabled={busy || dirty}
          onChange={onSelect}
          options={tasks.map((item) => ({
            value: item.id,
            label: `${new Date(item.created_at).toLocaleString()} · ${item.id.slice(0, 8)}${item.based_on_run_id ? " · 人工调整版" : ""}`,
          }))}
        />
      )}
      {error && <ErrorNotice message={error} onRetry={onRefresh} />}
      {!run ? (
        runId ? (
          <LoadingState label="读取任务…" />
        ) : (
          <EmptyState
            title="等待第一次分析"
            description="选择左侧的数据表，再点击“分析所选表”。"
          />
        )
      ) : (
        <>
          <div className="run-progress" role="status">
            {run.status === "analyzing" || run.graph_status === "building" ? (
              <LoadingState label={taskStatus(run)} />
            ) : (
              <p>
                <Tag
                  color={
                    run.graph_status === "ready"
                      ? "success"
                      : run.status === "failed" ||
                          run.graph_status === "failed" ||
                          run.result?.tables.some((t) => t.status === "failed")
                        ? "error"
                        : "processing"
                  }
                >
                  {taskStatus(run)}
                </Tag>
              </p>
            )}
          </div>
          {run.error && <ErrorNotice message={run.error} />}
          {run.graph_error && <ErrorNotice message={run.graph_error} />}
          {run.result && (
            <div className="task-sources">
              <strong>本任务的数据 · {run.result.tables.length} 张表</strong>
              <ul>
                {run.result.tables.map((table) => {
                  const active = table.trace?.steps.find(
                    (step) => step.status === "running",
                  );
                  return (
                    <li key={table.table_id}>
                      <div className="task-source-detail">
                        <span>{table.table_name}</span>
                        {table.status === "failed" ? (
                          <span className="task-source-error">
                            分析失败：{table.reason}
                          </span>
                        ) : active ? (
                          <small>{active.detail}</small>
                        ) : run.status === "analyzing" ? (
                          <small>
                            {table.trace?.steps.every(
                              (step) => step.status === "completed",
                            )
                              ? "字段分析与匹配已完成"
                              : "等待分析"}
                          </small>
                        ) : null}
                      </div>
                      <small>
                        {table.row_count.toLocaleString()} 行 ·{" "}
                        {table.columns.length.toLocaleString()} 列 · 批次{" "}
                        {table.batch_id.slice(0, 8)}
                      </small>
                    </li>
                  );
                })}
              </ul>
              {selections.length > 0 &&
                !sameSources(selections, run.result) && (
                  <Alert
                    type="info"
                    showIcon
                    title="左侧选择与当前任务不同"
                    description="点击“分析所选表”会创建新任务；当前结果仍使用上面列出的数据。"
                  />
                )}
            </div>
          )}
          {run.based_on_run_id && (
            <p className="hint">
              人工调整版 · 来源任务 {run.based_on_run_id.slice(0, 8)} ·
              原任务保留
            </p>
          )}
          {dirty && (
            <p className="hint">
              生成规则有未保存修改。请先确认规则或撤销修改，再切换任务。
            </p>
          )}
        </>
      )}
    </Panel>
  );
}
