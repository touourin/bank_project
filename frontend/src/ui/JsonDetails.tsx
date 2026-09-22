import { Collapse } from "antd";

export const pretty = (value: unknown) =>
  typeof value === "string" ? value : (JSON.stringify(value, null, 2) ?? "—");

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
