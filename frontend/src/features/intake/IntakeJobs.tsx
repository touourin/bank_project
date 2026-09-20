import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Space, Tag } from "antd";
import { Panel } from "../../ui/Panel";
import { ErrorNotice } from "../../ui/Feedback";
import { errorMessage, intakeApi } from "./api";
import type { BatchDetail, IntakeJob } from "./types";

const labels: Record<IntakeJob["status"], string> = {
  queued: "等待处理",
  running: "解析并暂存",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};
export function IntakeJobs({
  token,
  revision,
  onSaved,
}: {
  token: string;
  revision: number;
  onSaved: (batch: BatchDetail) => void;
}) {
  const [jobs, setJobs] = useState<IntakeJob[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const seen = useRef(new Map<string, string>());
  const saved = useRef(onSaved);
  saved.current = onSaved;
  const refresh = useCallback(
    async (signal: AbortSignal) => {
      try {
        const items = await intakeApi.jobs(token, signal);
        for (const job of items) {
          const previous = seen.current.get(job.id);
          if (
            job.status === "completed" &&
            previous &&
            previous !== "completed" &&
            job.batch_id
          ) {
            const batch = await intakeApi.batch(token, job.batch_id, signal);
            saved.current(batch);
          }
          seen.current.set(job.id, job.status);
        }
        setJobs(items);
        setError("");
      } catch (reason) {
        if (!signal.aborted) setError(errorMessage(reason));
      }
    },
    [token],
  );
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      await refresh(controller.signal);
      if (!controller.signal.aborted) timer = setTimeout(poll, 2000);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [refresh, revision]);
  async function action(job: IntakeJob, kind: "retry" | "cancel") {
    setBusy(job.id);
    try {
      await intakeApi.jobAction(token, job.id, kind);
      await refresh(new AbortController().signal);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy("");
    }
  }
  if (!jobs.length && !error) return null;
  return (
    <Panel
      title="接入任务"
      description="文件上传后在后台处理；关闭页面不影响任务。"
      padded
    >
      {error && <ErrorNotice message={error} />}
      <div className="intake-job-list">
        {jobs.map((job) => (
          <div key={job.id} className="intake-job-row">
            <div>
              <strong>{job.name}</strong>
              <p className="hint">
                已暂存 {job.rows_done.toLocaleString()} 行 · 第 {job.attempt}{" "}
                次处理
              </p>
              {job.error && <p role="alert">{job.error}</p>}
            </div>
            <Space wrap>
              <Tag
                color={
                  job.status === "completed"
                    ? "success"
                    : job.status === "failed"
                      ? "error"
                      : "processing"
                }
              >
                {labels[job.status]}
              </Tag>
              {job.status === "completed" && job.batch_id && (
                <Button
                  size="small"
                  onClick={async () => {
                    try {
                      saved.current(
                        await intakeApi.batch(
                          token,
                          job.batch_id!,
                          new AbortController().signal,
                        ),
                      );
                    } catch (reason) {
                      setError(errorMessage(reason));
                    }
                  }}
                >
                  查看数据
                </Button>
              )}
              {["failed", "cancelled"].includes(job.status) && (
                <Button
                  size="small"
                  loading={busy === job.id}
                  onClick={() => void action(job, "retry")}
                >
                  重新处理
                </Button>
              )}
              {["queued", "running"].includes(job.status) && (
                <Button
                  size="small"
                  loading={busy === job.id}
                  onClick={() => void action(job, "cancel")}
                >
                  取消
                </Button>
              )}
            </Space>
          </div>
        ))}
      </div>
    </Panel>
  );
}
