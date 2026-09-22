import type { ReactNode } from "react";
import { Table } from "antd";

export type ReviewEntry = {
  id: string;
  createdAt: string;
  action: string;
  reviewer?: string;
  note?: string;
  version: string | number;
  entities?: string;
  details: ReactNode;
};

/** Source adapters supply content; pagination, attribution and evidence UI stay shared. */
export function ReviewHistory({
  entries,
  showEntities = false,
}: {
  entries: ReviewEntry[];
  showEntities?: boolean;
}) {
  return (
    <Table<ReviewEntry>
      rowKey="id"
      size="small"
      dataSource={entries}
      pagination={{ pageSize: 8 }}
      scroll={{ x: showEntities ? 850 : 650 }}
      locale={{ emptyText: "暂无人工修改记录" }}
      columns={[
        {
          title: "时间",
          dataIndex: "createdAt",
          render: (value: string) => new Date(value).toLocaleString(),
        },
        { title: "操作", dataIndex: "action" },
        ...(showEntities
          ? [
              {
                title: "涉及实体",
                dataIndex: "entities",
                render: (value?: string) => (
                  <span className="resolution-audit-entities">
                    {value || "—"}
                  </span>
                ),
              },
            ]
          : []),
        {
          title: "审核人",
          dataIndex: "reviewer",
          render: (value?: string) => value || "未填写",
        },
        {
          title: "备注",
          dataIndex: "note",
          render: (value?: string) => value || "—",
        },
        {
          title: "版本",
          dataIndex: "version",
          render: (value: string | number) => (
            <span className="review-version">{value}</span>
          ),
        },
      ]}
      expandable={{ expandedRowRender: (entry) => entry.details }}
    />
  );
}
