import { useCallback, useState } from "react";
import { Button, Checkbox, Collapse } from "antd";
import { useResource } from "../../hooks/useResource";
import { ErrorNotice, EmptyState, LoadingState } from "../../ui/Feedback";
import { PagePagination } from "../../ui/PagePagination";
import { Panel } from "../../ui/Panel";
import { intakeApi } from "../intake/api";
import type { Selection } from "./types";

function BatchTables({
  token,
  batchId,
  selections,
  onChange,
  disabled,
}: {
  token: string;
  batchId: string;
  selections: Selection[];
  onChange: (value: Selection[]) => void;
  disabled: boolean;
}) {
  const load = useCallback(
    (signal: AbortSignal) => intakeApi.batch(token, batchId, signal),
    [token, batchId],
  );
  const resource = useResource(load);
  if (resource.error)
    return <ErrorNotice message={resource.error} onRetry={resource.refresh} />;
  if (!resource.data) return <LoadingState label="读取表结构…" />;
  return (
    <div className="alignment-tables">
      {resource.data.tables.map((table) => {
        const checked = selections.some((s) => s.table_id === table.id);
        return (
          <Checkbox
            key={table.id}
            checked={checked}
            disabled={disabled || (!checked && selections.length >= 30)}
            onChange={(event) =>
              onChange(
                event.target.checked
                  ? [...selections, { batch_id: batchId, table_id: table.id }]
                  : selections.filter((s) => s.table_id !== table.id),
              )
            }
          >
            <strong>{table.name}</strong>
            <small>
              {table.row_count.toLocaleString()} 行 · {table.columns.length}{" "}
              个字段
            </small>
          </Checkbox>
        );
      })}
    </div>
  );
}

export function SourcePicker({
  token,
  selections,
  onChange,
  disabled,
}: {
  token: string;
  selections: Selection[];
  onChange: (value: Selection[]) => void;
  disabled: boolean;
}) {
  const [offset, setOffset] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => intakeApi.batches(token, offset, signal),
    [token, offset],
  );
  const batches = useResource(load);
  return (
    <Panel
      title="选择数据 · 发起新分析"
      description={`已选 ${selections.length} / 30 张表`}
      actions={
        <Button
          size="small"
          disabled={disabled}
          onClick={() => {
            onChange([]);
            batches.refresh();
          }}
        >
          刷新列表
        </Button>
      }
      padded
    >
      {batches.error ? (
        <ErrorNotice message={batches.error} onRetry={batches.refresh} />
      ) : !batches.data ? (
        <LoadingState />
      ) : !batches.data.items.length ? (
        <EmptyState
          title="还没有接入数据"
          description="请先到第一步上传文件或选择 MySQL 表。"
        />
      ) : (
        <>
          <Collapse
            size="small"
            items={batches.data.items.map((batch) => ({
              key: batch.id,
              label: (
                <span className="alignment-batch-name">
                  {batch.name}
                  <small>
                    {new Date(batch.created_at).toLocaleString()} ·{" "}
                    {batch.id.slice(0, 8)}
                  </small>
                </span>
              ),
              children: (
                <BatchTables
                  token={token}
                  batchId={batch.id}
                  selections={selections}
                  onChange={onChange}
                  disabled={disabled}
                />
              ),
            }))}
          />
          <PagePagination
            offset={offset}
            pageSize={10}
            total={batches.data.total}
            onChange={setOffset}
            unit="个批次"
          />
        </>
      )}
      {selections.length > 0 && (
        <Button type="link" disabled={disabled} onClick={() => onChange([])}>
          清空选择
        </Button>
      )}
    </Panel>
  );
}
