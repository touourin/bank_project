import { Collapse } from "antd";
import { pretty } from "./shared";
import type { ResolutionCandidate, SourceKind } from "./types";

type Difference = ResolutionCandidate["conflicts"][number];
type Column = { id: string; label: string };

const graphMetadata = new Set([
  "id",
  "human_readable_id",
  "text_unit_ids",
  "degree",
  "frequency",
]);
const fieldLabels: Record<string, string> = {
  title: "名称",
  name: "名称",
  type: "类型",
  description: "描述",
  aliases: "别名",
  "@type": "实体类型",
  "@boid": "本体概念",
  human_readable_id: "图谱序号",
  text_unit_ids: "原文分块编号",
  degree: "关联数量",
  frequency: "出现次数",
};

function FieldValue({ value }: { value: unknown }) {
  if (value == null) return <span className="hint">未提供</span>;
  if (value === "") return <span className="hint">空文本</span>;
  if (typeof value === "boolean") return <>{value ? "是" : "否"}</>;
  if (Array.isArray(value))
    return value.length ? (
      <ul className="resolution-value-list">
        {value.map((item, index) => (
          <li key={index}>
            <FieldValue value={item} />
          </li>
        ))}
      </ul>
    ) : (
      <span className="hint">空列表</span>
    );
  return <span className="resolution-value">{pretty(value)}</span>;
}

function ComparisonTable({
  rows,
  columns,
  sourceKind,
  technical = false,
}: {
  rows: Difference[];
  columns: Column[];
  sourceKind: SourceKind;
  technical?: boolean;
}) {
  function label(field: string) {
    if (sourceKind === "graphrag") {
      if (field === "description") return "描述（抽取结果）";
      if (field === "type") return "抽取类型";
      if (field === "id") return "节点编号";
      return fieldLabels[field] ?? field;
    }
    return graphMetadata.has(field) ? field : (fieldLabels[field] ?? field);
  }
  return (
    <table
      className={`resolution-comparison-table${columns.length > 3 ? " is-stacked" : ""}`}
      aria-label={technical ? "系统记录差异" : "内容差异"}
    >
      <thead>
        <tr>
          <th scope="col">对比项</th>
          {columns.map((column) => (
            <th scope="col" key={column.id} title={`节点编号：${column.id}`}>
              {column.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.field}>
            <th scope="row" title={`原始字段：${row.field}`}>
              {label(row.field)}
              {technical && <small className="hint">{row.field}</small>}
            </th>
            {columns.map((column) => {
              const values = row.values.filter(
                (item) => item.node_id === column.id,
              );
              return (
                <td key={column.id}>
                  <span className="resolution-comparison-mobile-label">
                    {column.label}
                  </span>
                  {values.length ? (
                    values.map((item, index) => (
                      <div key={index} className="resolution-comparison-value">
                        {values.length > 1 && (
                          <small className="hint">来源值 {index + 1}</small>
                        )}
                        <FieldValue value={item.value} />
                      </div>
                    ))
                  ) : (
                    <FieldValue value={undefined} />
                  )}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ResolutionDifferences({
  candidate,
  sourceKind,
}: {
  candidate: ResolutionCandidate;
  sourceKind: SourceKind;
}) {
  if (!candidate.conflicts.length) return null;
  const content: Difference[] = [];
  const technical: Difference[] = [];
  for (const row of candidate.conflicts)
    (sourceKind === "graphrag" && graphMetadata.has(row.field)
      ? technical
      : content
    ).push(row);
  const order = ["title", "name", "@type", "description"];
  const rank = (field: string) => {
    const index = order.indexOf(field);
    return index < 0 ? order.length : index;
  };
  content.sort((a, b) => rank(a.field) - rank(b.field));
  const columns: Column[] = candidate.nodes.map((node, index) => ({
    id: node.id,
    label: `${index === 0 ? "左侧" : index === 1 ? "右侧" : `实体 ${index + 1}`} · ${node.name}`,
  }));
  const knownIds = new Set(columns.map((column) => column.id));
  let extra = 0;
  for (const row of candidate.conflicts)
    for (const value of row.values)
      if (!knownIds.has(value.node_id)) {
        knownIds.add(value.node_id);
        columns.push({ id: value.node_id, label: `来源记录 ${++extra}` });
      }
  return (
    <section className="resolution-differences" aria-label="差异对照">
      <h4>内容差异（{content.length} 项）</h4>
      <p className="hint">
        {content.length
          ? "请结合上方原文，判断这些内容差异是否影响实体身份。"
          : "本组差异均为系统记录信息。"}
      </p>
      {content.length > 0 && (
        <ComparisonTable
          rows={content}
          columns={columns}
          sourceKind={sourceKind}
        />
      )}
      {technical.length > 0 && (
        <Collapse
          size="small"
          items={[
            {
              key: "technical",
              label: `系统记录差异（${technical.length} 项）`,
              children: (
                <>
                  <p className="hint">
                    节点编号、来源分块和图谱统计单独保留，供追溯核对。
                  </p>
                  <ComparisonTable
                    rows={technical}
                    columns={columns}
                    sourceKind={sourceKind}
                    technical
                  />
                </>
              ),
            },
          ]}
        />
      )}
    </section>
  );
}
