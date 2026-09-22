import { Tag } from "antd";

const labels: Record<string, [string, string]> = {
  running: ["生成中", "processing"],
  succeeded: ["已完成", "green"],
  failed: ["失败", "red"],
  pending_review: ["待审核", "gold"],
  approved: ["已批准", "green"],
  rejected: ["已驳回", "red"],
};

export function RiskStatus({
  status,
  runningLabel = "生成中",
}: {
  status: string;
  runningLabel?: string;
}) {
  const [label, color] = labels[status] ?? [status, "default"];
  return <Tag color={color}>{status === "running" ? runningLabel : label}</Tag>;
}

export function RiskJson({ value, label }: { value: unknown; label: string }) {
  return (
    <details className="risk-details">
      <summary>{label}</summary>
      <pre className="risk-json">{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

export const fieldLabels: Record<string, string> = {
  account_id: "账户编号",
  occurred_at: "交易时间（含时区）",
  amount: "交易金额（人民币元）",
  txn_type: "交易类型",
  status: "交易状态",
  counterparty_region: "交易对手地区",
};
