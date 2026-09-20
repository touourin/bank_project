import { Tag, Tooltip } from "antd";
import type { ColumnMapping } from "./types";

/** Current field handling is separate from the immutable retrieval outcome. */
export function ColumnHandling({ column }: { column: ColumnMapping }) {
  if (column.role === "ignore") return <Tag>已忽略字段</Tag>;
  if (column.concept_id) return <Tag color="success">已指定节点</Tag>;
  return (
    <Tooltip title="保留原字段名和原值作为普通属性，无需补选本体节点。">
      <Tag color="blue">默认属性</Tag>
    </Tooltip>
  );
}
