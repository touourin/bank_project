import { Button } from "antd";
import { Panel } from "../../ui/Panel";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { StatusTag } from "./shared";
import type { Dataset } from "./types";

export function TextDatasetPicker({
  datasets,
  selected,
  loading,
  error,
  onSelect,
  onRefresh,
}: {
  datasets?: Dataset[];
  selected?: string;
  loading: boolean;
  error?: string;
  onSelect: (key: string) => void;
  onRefresh: () => void;
}) {
  return (
    <Panel
      title="文本数据集"
      description="选择第一步已接入的文本，查看转换任务及结果。"
      actions={
        <Button size="small" onClick={onRefresh}>
          刷新
        </Button>
      }
      padded
    >
      {loading && !datasets && <LoadingState />}
      {error && <ErrorNotice message={error} onRetry={onRefresh} />}
      {datasets?.length === 0 && (
        <EmptyState
          title="暂无文本"
          description="请先在第一步选择 TXT 文本接入。"
        />
      )}
      <div className="knowledge-history">
        {datasets?.map((item) => (
          <button
            key={item.key}
            aria-pressed={item.key === selected}
            onClick={() => onSelect(item.key)}
          >
            <strong>{item.name}</strong>
            <small>
              <StatusTag status={item.status} />
              {item.stage}
            </small>
          </button>
        ))}
      </div>
    </Panel>
  );
}
