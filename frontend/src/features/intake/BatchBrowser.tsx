import { useCallback, useEffect, useState } from "react";
import { Button, Collapse, Select, Tag } from "antd";
import { Database, FileSpreadsheet, RefreshCw, Trash2 } from "lucide-react";
import { useResource } from "../../hooks/useResource";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { PagePagination } from "../../ui/PagePagination";
import { Panel } from "../../ui/Panel";
import { intakeApi } from "./api";
import { TablePreview } from "./TablePreview";
import type { BatchInfo } from "./types";

export function BatchBrowser({
  token,
  savedId,
  revision,
}: {
  token: string;
  savedId: string;
  revision: number;
}) {
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState(savedId);
  const [tableId, setTableId] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Pick<
    BatchInfo,
    "id" | "name"
  > | null>(null);
  const loadList = useCallback(
    (signal: AbortSignal) => intakeApi.batches(token, offset, signal),
    [token, offset],
  );
  const batches = useResource(loadList);
  const loadDetail = useCallback(
    (signal: AbortSignal) =>
      selectedId
        ? intakeApi.batch(token, selectedId, signal)
        : Promise.resolve(null),
    [token, selectedId],
  );
  const detail = useResource(loadDetail);
  useEffect(() => {
    setOffset(0);
    setSelectedId(savedId);
    batches.refresh();
  }, [savedId, revision, batches.refresh]);
  useEffect(() => {
    setTableId("");
  }, [selectedId]);
  const batch = detail.data;
  const table =
    batch?.tables.find((item) => item.id === tableId) || batch?.tables[0];
  return (
    <div className="batch-workspace">
      <Panel
        className="batches-panel"
        title={
          <>
            接入记录 <Tag color="success">{batches.data?.total ?? "—"}</Tag>
          </>
        }
        description="每次接入独立保存，可随时查看原始数据。"
        actions={
          <Button
            aria-label="刷新接入记录"
            icon={<RefreshCw size={17} />}
            loading={batches.loading}
            onClick={() => {
              batches.refresh();
              detail.refresh();
            }}
          />
        }
      >
        {batches.error ? (
          <ErrorNotice message={batches.error} onRetry={batches.refresh} />
        ) : batches.loading ? (
          <LoadingState label="正在读取接入记录…" />
        ) : !batches.data?.total ? (
          <EmptyState
            title="从第一份数据开始"
            description={
              <>
                上传表格，或连接 MySQL。接入成功后，
                <br />
                可以在这里查看各张表的字段与数据。
              </>
            }
          />
        ) : (
          <>
            <div className="batch-list">
              {batches.data.items.map((item) => (
                <button
                  key={item.id}
                  className={`batch-row ${selectedId === item.id ? "selected" : ""}`}
                  onClick={() => setSelectedId(item.id)}
                  aria-pressed={selectedId === item.id}
                >
                  <span className="source-icon">
                    {item.source_kind === "file" ? (
                      <FileSpreadsheet size={20} />
                    ) : (
                      <Database size={20} />
                    )}
                  </span>
                  <span className="batch-name">
                    <strong>{item.name}</strong>
                    <small>
                      {new Date(item.created_at).toLocaleString("zh-CN", {
                        hour12: false,
                      })}{" "}
                      · 批次 {item.id.slice(0, 8)}
                    </small>
                  </span>
                  <span className="batch-metrics">
                    {item.table_count} 张表{" "}
                    <small>{item.row_count.toLocaleString()} 行</small>
                  </span>
                </button>
              ))}
            </div>
            {batches.data.total > batches.data.limit && (
              <PagePagination
                offset={offset}
                pageSize={batches.data.limit}
                total={batches.data.total}
                unit="个批次"
                onChange={setOffset}
              />
            )}
            {!selectedId && (
              <p className="hint select-hint">选择一条接入记录，查看数据。</p>
            )}
          </>
        )}
      </Panel>
      {selectedId && (
        <Panel
          className="detail-panel"
          title={batch?.name}
          eyebrow={batch ? `BATCH / ${batch.id.slice(0, 8)}` : undefined}
          description={batch && <>来源：{batch.source}</>}
          actions={
            batch && (
              <Button
                danger
                aria-label="删除当前批次"
                icon={<Trash2 size={17} />}
                onClick={() =>
                  setPendingDelete({ id: batch.id, name: batch.name })
                }
              />
            )
          }
        >
          {detail.error ? (
            <ErrorNotice message={detail.error} onRetry={detail.refresh} />
          ) : !batch ? (
            <LoadingState label="正在读取批次…" />
          ) : (
            <>
              <div className="table-picker">
                <label htmlFor="batch-table">数据表</label>
                <Select
                  id="batch-table"
                  value={table?.id}
                  onChange={setTableId}
                  options={batch.tables.map((item) => ({
                    value: item.id,
                    label: `${item.name}（${item.row_count.toLocaleString()} 行）`,
                  }))}
                />
              </div>
              {batch.warnings.length > 0 && (
                <Collapse
                  className="import-notes"
                  size="small"
                  items={[
                    {
                      key: "warnings",
                      label: `读取说明（${batch.warnings.length}）`,
                      children: batch.warnings.map((warning, index) => (
                        <p key={index}>{warning}</p>
                      )),
                    },
                  ]}
                />
              )}
              {table && (
                <TablePreview
                  key={table.id}
                  token={token}
                  batchId={batch.id}
                  table={table}
                />
              )}
            </>
          )}
        </Panel>
      )}
      {pendingDelete && (
        <ConfirmDialog
          title="删除接入批次"
          confirmLabel="删除批次"
          danger
          onClose={() => setPendingDelete(null)}
          onConfirm={async () => {
            await intakeApi.remove(token, pendingDelete.id);
            setSelectedId((current) =>
              current === pendingDelete.id ? "" : current,
            );
            setOffset(0);
            batches.refresh();
          }}
        >
          <p>确定从接入列表移除「{pendingDelete.name}」？</p>
          <p className="hint">
            原始文件和源数据库不受影响；MySQL
            暂存版本会保留，以便历史分析继续使用。
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
