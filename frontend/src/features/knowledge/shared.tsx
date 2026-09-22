import { Tag } from "antd";
import { JsonDetails } from "../../ui/JsonDetails";
import { ReviewHistory } from "../conversion/ReviewHistory";
import { taskPhase, taskColor } from "../conversion/taskState";
export { JsonDetails, pretty } from "../../ui/JsonDetails";
import type { Audit } from "./types";

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
        ["merged", "matched", "ready"].includes(status)
          ? "success"
          : taskColor(taskPhase(status))
      }
    >
      {statusLabel(status)}
    </Tag>
  );
}
export function AuditTable({
  audits,
  showEntities = false,
}: {
  audits: Audit[];
  showEntities?: boolean;
}) {
  return (
    <ReviewHistory
      showEntities={showEntities}
      entries={audits.map((row) => ({
        id: row.id,
        createdAt: row.created_at,
        action:
          row.action === "reset" && row.previous_status === "merged"
            ? "退回合并"
            : ({
                accept_proposal: "采纳匹配建议",
                refresh_endpoints: "端点变更后重新校验",
                merge: "合并",
                manual: "人工指定合并",
                reject: "保留独立节点",
                reset: "撤销决定",
              }[row.action ?? ""] ??
              (row.target === "edge" ? "修改边类型" : "修改节点")),
        entities: row.source_nodes?.map((node) => node.name).join(" / "),
        reviewer: row.reviewer,
        note: row.note,
        version: row.revision,
        details: <JsonDetails value={row} label="修改前后详情" />,
      }))}
    />
  );
}
