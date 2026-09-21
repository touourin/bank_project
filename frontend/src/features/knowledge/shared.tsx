import { useEffect } from "react";
import { Collapse, Table, Tag } from "antd";
import type { Audit } from "./types";

export function usePolling(active: boolean, refresh: () => void, delay = 2500) {
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(refresh, delay);
    return () => window.clearInterval(timer);
  }, [active, refresh, delay]);
}
export const pretty = (value: unknown) =>
  typeof value === "string" ? value : (JSON.stringify(value, null, 2) ?? "—");
export const statusLabel = (status: string) =>
  ({
    uploaded: "已接收",
    queued: "排队中",
    succeeded: "索引完成",
    interrupted: "已中断",
    pending: "待审核",
    indexing: "索引中",
    running: "处理中",
    analyzing: "分析中",
    ready: "已就绪",
    completed: "已完成",
    failed: "失败",
    merged: "已合并",
    rejected: "已保留",
    excluded: "自动不合并",
    not_recommended: "未建议合并",
    matched: "已匹配",
    review: "待确认",
    unmatched: "未匹配",
    unavailable: "不可用",
  })[status] ?? status;
export function StatusTag({ status }: { status: string }) {
  return (
    <Tag
      color={
        status === "failed"
          ? "error"
          : ["ready", "completed", "merged", "matched"].includes(status)
            ? "success"
            : "default"
      }
    >
      {statusLabel(status)}
    </Tag>
  );
}
export function JsonDetails({
  value,
  label = "查看原始信息",
}: {
  value: unknown;
  label?: string;
}) {
  return (
    <Collapse
      size="small"
      items={[
        {
          key: "details",
          label,
          children: <pre className="knowledge-json">{pretty(value)}</pre>,
        },
      ]}
    />
  );
}
export function AuditTable({ audits }: { audits: Audit[] }) {
  return (
    <Table
      rowKey="id"
      size="small"
      dataSource={audits}
      pagination={{ pageSize: 8 }}
      scroll={{ x: 650 }}
      locale={{ emptyText: "暂无人工修改记录" }}
      columns={[
        {
          title: "时间",
          dataIndex: "created_at",
          render: (v: string) => new Date(v).toLocaleString(),
        },
        {
          title: "操作",
          render: (_, row) =>
            ({
              accept_proposal: "采纳匹配建议",
              refresh_endpoints: "端点变更后重新校验",
              merge: "合并",
              manual: "人工指定合并",
              reject: "保留独立节点",
              reset: "撤销决定",
            })[row.action ?? ""] ??
            (row.target === "edge" ? "修改边类型" : "修改节点"),
        },
        {
          title: "审核人",
          dataIndex: "reviewer",
          render: (v: string) => v || "未填写",
        },
        { title: "备注", dataIndex: "note", render: (v: string) => v || "—" },
        { title: "版本", dataIndex: "revision" },
      ]}
      expandable={{
        expandedRowRender: (row) => (
          <JsonDetails value={row} label="修改前后详情" />
        ),
      }}
    />
  );
}
