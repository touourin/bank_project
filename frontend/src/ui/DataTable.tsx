import { Table, type TableProps } from "antd";

/** Server paging is handled separately; never slice the supplied page a second time. */
export function DataTable<Row extends object>({
  label,
  ...props
}: Omit<TableProps<Row>, "pagination" | "size"> & { label: string }) {
  return (
    <div
      className="ui-data-table"
      role="region"
      aria-label={label}
      tabIndex={0}
    >
      <Table<Row>
        size="small"
        scroll={{ x: "max-content", y: 420 }}
        {...props}
        pagination={false}
      />
    </div>
  );
}
