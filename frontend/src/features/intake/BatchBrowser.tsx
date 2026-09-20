import { useCallback, useEffect, useState } from "react";
import { Button, Collapse, Select, Tag, Segmented } from "antd";
import { Database, FileSpreadsheet, RefreshCw } from "lucide-react";
import { useResource } from "../../hooks/useResource";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { PagePagination } from "../../ui/PagePagination";
import { Panel } from "../../ui/Panel";
import { intakeApi } from "./api";
import { TablePreview } from "./TablePreview";
import { BatchActions } from "./BatchActions";

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
  const [removed, setRemoved] = useState(false);
  const [notice, setNotice] = useState("");
  const loadList = useCallback(
    (signal: AbortSignal) => intakeApi.batches(token, offset, signal, removed),
    [token, offset, removed],
  );
  const batches = useResource(loadList, true);
  const loadDetail = useCallback(
    (signal: AbortSignal) =>
      selectedId
        ? intakeApi.batch(token, selectedId, signal, removed)
        : Promise.resolve(null),
    [token, selectedId, removed],
  );
  const detail = useResource(loadDetail);
  useEffect(() => {
    setOffset(0);
    setSelectedId(savedId);
    setRemoved(false);
    batches.refresh();
  }, [savedId, revision, batches.refresh]);
  useEffect(() => {
    setTableId("");
  }, [selectedId]);
  const cleaning = batches.data?.items.some((item) => item.purging);
  useEffect(() => {
    if (!cleaning) return;
    const timer = setInterval(batches.refresh, 3000);
    return () => clearInterval(timer);
  }, [cleaning, batches.refresh]);
  const changed = (message: string) => {
    setNotice(message);
    setSelectedId("");
    setOffset(0);
    batches.refresh();
  };
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
        description={
          removed
            ? "已移除的数据仍占用空间，可恢复或检查后彻底删除。"
            : "每次接入独立保存，可查看数据或移除批次。"
        }
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
        <div className="batch-lifecycle-toolbar">
          <Segmented
            aria-label="接入记录范围"
            value={removed ? "removed" : "active"}
            options={[
              { label: "可用数据", value: "active" },
              { label: "已移除", value: "removed" },
            ]}
            onChange={(value) => {
              setRemoved(value === "removed");
              setOffset(0);
              setSelectedId("");
              setNotice("");
            }}
          />
          {notice && (
            <p role="status" className="hint">
              {notice}
            </p>
          )}
        </div>
        {batches.error ? (
          <ErrorNotice message={batches.error} onRetry={batches.refresh} />
        ) : batches.loading && !batches.data ? (
          <LoadingState label="正在读取接入记录…" />
        ) : !batches.data?.total ? (
          <EmptyState
            title={removed ? "没有已移除的数据" : "从第一份数据开始"}
            description={
              removed ? (
                "移除的批次会出现在这里。"
              ) : (
                <>
                  上传表格，或连接 MySQL。接入成功后，
                  <br />
                  可以在这里查看各张表的字段与数据。
                </>
              )
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
                    {item.purging && <Tag color="processing">后台清理中</Tag>}
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
              <BatchActions
                key={batch.id}
                token={token}
                batch={batch}
                onChanged={changed}
              />
            )
          }
        >
          {detail.error ? (
            <ErrorNotice message={detail.error} onRetry={detail.refresh} />
          ) : !batch ? (
            <LoadingState label="正在读取批次…" />
          ) : batch.purging ? (
            <p className="hint batch-lifecycle-toolbar">
              正在后台分批清理，完成后该记录会消失。清理期间无法预览或恢复数据。
            </p>
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
                  removed={batch.removed}
                />
              )}
            </>
          )}
        </Panel>
      )}
    </div>
  );
}
