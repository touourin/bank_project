import { useCallback } from "react";
import { Alert, Button, Progress } from "antd";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { useResource } from "../../hooks/useResource";
import { Panel } from "../../ui/Panel";
import { ErrorNotice } from "../../ui/Feedback";
import { knowledgeApi } from "./api";
import { isDatasetReady, isDatasetRunning } from "./useTextDatasets";
import type { Dataset } from "./types";

export function TextIndexPanel({
  token,
  dataset,
  onRefresh,
}: {
  token: string;
  dataset?: Dataset;
  onRefresh: () => void;
}) {
  const config = useResource(
    useCallback(
      (signal: AbortSignal) => knowledgeApi.config(token, signal),
      [token],
    ),
  );
  const { busy, error, execute } = useAsyncAction();
  const ready = isDatasetReady(dataset);
  async function start() {
    if (dataset)
      await execute("convert", () => knowledgeApi.index(token, dataset.key), {
        onSettled: onRefresh,
      });
  }
  return (
    <Panel title="文本转换任务" padded>
      {config.error && (
        <ErrorNotice message={config.error} onRetry={config.refresh} />
      )}
      {config.data?.error && (
        <Alert type="warning" showIcon title={config.data.error} />
      )}
      <p className="hint">
        按已选方案分块、抽取实体与关系，并生成社区和检索索引。转换会调用已配置的模型，完成后可核对本体匹配。
      </p>
      {dataset && (
        <>
          <p>
            <strong>{dataset.name}</strong>
          </p>
          <p className="hint">
            {dataset.profile === "enterprise_zh"
              ? "企业情报 · 中文方案"
              : "通用文档方案"}
          </p>
          <p>{dataset.stage || "文本已接收"}</p>
          {typeof dataset.progress === "number" ? (
            <Progress
              percent={Math.round(dataset.progress * 100)}
              status={
                dataset.status === "failed"
                  ? "exception"
                  : ready
                    ? "success"
                    : "active"
              }
            />
          ) : (
            <p className="hint">{dataset.progress}</p>
          )}
          {dataset.error && <ErrorNotice message={dataset.error} />}
        </>
      )}
      <Button
        block
        type="primary"
        loading={busy}
        disabled={
          !dataset ||
          ready ||
          isDatasetRunning(dataset) ||
          !config.data ||
          config.data.configured === false
        }
        onClick={() => void start()}
      >
        {dataset?.status === "failed" || dataset?.status === "interrupted"
          ? "重试文本转换"
          : ready
            ? "文本转换已完成"
            : "开始文本转换"}
      </Button>
      {error && <ErrorNotice message={error} />}
    </Panel>
  );
}
