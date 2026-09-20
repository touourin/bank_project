import { useCallback, useMemo, useState } from "react";
import { Segmented, type TableColumnsType } from "antd";
import { useResource } from "../../hooks/useResource";
import { DataTable } from "../../ui/DataTable";
import { EmptyState, ErrorNotice } from "../../ui/Feedback";
import { PagePagination } from "../../ui/PagePagination";
import { intakeApi } from "./api";
import type { Column, TableInfo, TablePage } from "./types";

type DataRow = TablePage["rows"][number];
const schemaColumns: TableColumnsType<Column> = [
  {
    title: "字段名称",
    dataIndex: "name",
    render: (value: string) => <div className="cell-value">{value}</div>,
  },
  { title: "类型", dataIndex: "data_type" },
  {
    title: "依据",
    dataIndex: "type_origin",
    render: (value: Column["type_origin"]) =>
      value === "declared" ? "数据库声明" : "数据观察",
  },
  {
    title: "主键",
    dataIndex: "primary_key",
    render: (value: boolean) => (value ? "是" : "—"),
  },
  {
    title: "允许空值 / 已观察空值",
    dataIndex: "nullable",
    render: (value: boolean) => (value ? "是" : "否"),
  },
  {
    title: "字段说明",
    dataIndex: "comment",
    render: (value: string) => <div className="cell-value">{value || "—"}</div>,
  },
];

function CellValue({ value }: { value: string | null }) {
  return (
    <div className="cell-value" title={value ?? "NULL"}>
      {value === null ? (
        <span className="null-value">NULL</span>
      ) : value === "" ? (
        <span className="null-value">空文本</span>
      ) : (
        value
      )}
    </div>
  );
}

export function TablePreview({
  token,
  batchId,
  table,
  removed = false,
}: {
  token: string;
  batchId: string;
  table: TableInfo;
  removed?: boolean;
}) {
  const [offset, setOffset] = useState(0);
  const [view, setView] = useState<"rows" | "schema">("rows");
  const load = useCallback(
    (signal: AbortSignal) =>
      intakeApi.table(token, batchId, table.id, offset, signal, removed),
    [token, batchId, table.id, offset, removed],
  );
  const resource = useResource(load);
  const columns = useMemo<TableColumnsType<DataRow>>(
    () => [
      {
        title: "来源行号",
        key: "row-number",
        dataIndex: "number",
        width: 100,
        className: "row-number",
      },
      ...table.columns.map((column, index) => ({
        title: <span title={column.comment}>{column.name}</span>,
        key: `column-${index}`,
        render: (_: unknown, row: DataRow) => (
          <CellValue value={row.values[index]} />
        ),
      })),
    ],
    [table.columns],
  );
  return (
    <section className="table-preview" aria-label="数据预览">
      <div className="preview-toolbar">
        <Segmented<"rows" | "schema">
          aria-label="预览方式"
          value={view}
          onChange={setView}
          options={[
            { label: "数据预览", value: "rows" },
            { label: "字段结构", value: "schema" },
          ]}
        />
        <span className="hint">
          {table.row_count.toLocaleString()} 行 · {table.columns.length} 列
        </span>
      </div>
      {view === "rows" ? (
        <>
          {resource.error ? (
            <ErrorNotice message={resource.error} onRetry={resource.refresh} />
          ) : (
            <DataTable<DataRow>
              label="原始数据表格"
              rowKey="number"
              columns={columns}
              dataSource={resource.data?.rows ?? []}
              loading={resource.loading}
              locale={{
                emptyText: (
                  <EmptyState
                    title={
                      resource.loading
                        ? "正在读取数据…"
                        : "这张表只有字段结构，没有数据行。"
                    }
                  />
                ),
              }}
            />
          )}
          <PagePagination
            offset={offset}
            pageSize={25}
            total={table.row_count}
            loading={resource.loading}
            onChange={setOffset}
          />
        </>
      ) : (
        <>
          <p className="hint schema-hint">
            文件字段类型是观察结果，不会据此改写原始值。MySQL
            字段类型及主键来自数据库声明。
          </p>
          <DataTable<Column>
            label="字段结构表格"
            rowKey="name"
            columns={schemaColumns}
            dataSource={table.columns}
          />
          {table.foreign_keys.length > 0 && (
            <div className="foreign-keys">
              <strong>数据库声明的外键</strong>
              {table.foreign_keys.map((key) => (
                <p key={key.name}>
                  {key.columns.join(" + ")} → {key.target_schema}.
                  {key.target_table}（{key.target_columns.join(" + ")}）
                </p>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
