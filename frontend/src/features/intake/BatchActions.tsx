import { useCallback, useState } from "react";
import { Button, Space, Tag } from "antd";
import { Archive, RotateCcw, Trash2 } from "lucide-react";
import { useResource } from "../../hooks/useResource";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { ErrorNotice, LoadingState } from "../../ui/Feedback";
import { intakeApi } from "./api";
import type { BatchInfo } from "./types";

type Props = {
  token: string;
  batch: BatchInfo;
  onChanged: (notice: string) => void;
};

function PurgeDialog({
  token,
  batch,
  onChanged,
  onClose,
}: Props & { onClose: () => void }) {
  const load = useCallback(
    (signal: AbortSignal) => intakeApi.references(token, batch.id, signal),
    [token, batch.id],
  );
  const references = useResource(load);
  return (
    <ConfirmDialog
      title="彻底删除数据"
      confirmLabel="彻底删除"
      danger
      disabled={
        references.loading || !references.data || references.data.count > 0
      }
      onClose={onClose}
      onConfirm={async () => {
        const result = await intakeApi.purge(token, batch.id);
        onChanged(
          result.status === "purging"
            ? "已提交后台清理。关闭页面不影响处理，完成后记录会从已移除列表消失。"
            : "已彻底删除暂存数据。",
        );
      }}
    >
      <p>
        「{batch.name}」包含 {batch.table_count} 张表、
        {batch.row_count.toLocaleString()} 行。
      </p>
      <p>
        将永久清理这个批次的暂存数据、服务器上传副本及对应接入任务，无法恢复。电脑上的原文件和源数据库不受影响。
      </p>
      {references.loading ? (
        <LoadingState label="正在检查历史分析引用…" />
      ) : references.error ? (
        <ErrorNotice message={references.error} onRetry={references.refresh} />
      ) : references.data && references.data.count > 0 ? (
        <div role="alert">
          <p>
            该批次被 {references.data.count}{" "}
            个历史分析版本引用，不能彻底删除。可继续保留在已移除列表。
          </p>
          <p className="hint">
            引用任务：
            {references.data.run_ids.map((id) => id.slice(0, 8)).join("、")}
            {references.data.count > references.data.run_ids.length
              ? " 等"
              : ""}
          </p>
        </div>
      ) : (
        <p className="hint">未发现历史分析引用；确认时还会再次检查。</p>
      )}
    </ConfirmDialog>
  );
}

export function BatchActions({ token, batch, onChanged }: Props) {
  const [action, setAction] = useState<"remove" | "restore" | "purge" | null>(
    null,
  );
  if (batch.purging) return <Tag color="processing">后台清理中</Tag>;
  return (
    <>
      <Space wrap>
        {batch.removed ? (
          <>
            <Button
              icon={<RotateCcw size={16} />}
              onClick={() => setAction("restore")}
            >
              恢复数据
            </Button>
            <Button
              danger
              icon={<Trash2 size={16} />}
              onClick={() => setAction("purge")}
            >
              彻底删除
            </Button>
          </>
        ) : (
          <Button
            icon={<Archive size={16} />}
            onClick={() => setAction("remove")}
          >
            移除数据
          </Button>
        )}
      </Space>
      {action === "purge" ? (
        <PurgeDialog
          token={token}
          batch={batch}
          onChanged={onChanged}
          onClose={() => setAction(null)}
        />
      ) : (
        action && (
          <ConfirmDialog
            title={action === "remove" ? "移除数据" : "恢复数据"}
            confirmLabel={action === "remove" ? "确认移除" : "确认恢复"}
            onClose={() => setAction(null)}
            onConfirm={async () => {
              if (action === "remove") {
                await intakeApi.remove(token, batch.id);
                onChanged("已移除。可在“已移除”列表中恢复，或检查后彻底删除。");
              } else {
                await intakeApi.restore(token, batch.id);
                onChanged("已恢复，可在“可用数据”列表中查看并发起分析。");
              }
            }}
          >
            <p>
              「{batch.name}」· {batch.table_count} 张表 ·{" "}
              {batch.row_count.toLocaleString()} 行
            </p>
            <p>
              {action === "remove"
                ? "移除后不再用于新分析，历史分析和暂存数据仍保留，不释放存储空间。可以随时恢复。"
                : "恢复后可重新选择该批次进行分析。"}
            </p>
          </ConfirmDialog>
        )
      )}
    </>
  );
}
