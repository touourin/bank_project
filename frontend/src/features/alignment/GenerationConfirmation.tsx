import type { Run } from "./types";
import { generationPlan, tableLabel } from "./workflow";

export function GenerationConfirmation({
  run,
  plan,
}: {
  run: Run;
  plan: ReturnType<typeof generationPlan>;
}) {
  return (
    <>
      <p>
        任务 {run.id.slice(0, 8)} · 本次采用 {plan.included.length} 张表，共{" "}
        {plan.rows.toLocaleString()} 行来源数据。
      </p>
      <ul>
        {plan.included.map((table) => (
          <li key={table.table_id}>
            {tableLabel(table)} · {table.row_count.toLocaleString()} 行
          </li>
        ))}
      </ul>
      <p>
        对象类型：{plan.concepts.join("、")}。关系规则：{plan.relationRules}{" "}
        条。
      </p>
      <p>
        {plan.suggestedNodes > 0 &&
          `包含 ${plan.suggestedNodes} 个低分或未验证的对象建议；采纳后仍保留原始得分与依据。`}
        普通字段未匹配时保留原始属性，无需逐项确认。
      </p>
      <p>
        实际节点与关系数量在生成后统计。
        {plan.relationRules === 0 && "当前未配置关系，将只生成节点。"}
      </p>
      {plan.excluded.length > 0 && (
        <p>不参与生成：{plan.excluded.map(tableLabel).join("、")}。</p>
      )}
      <p>
        按确认的规则识别对象并检查属性冲突。新图生成成功后切换当前版本，旧版本保留。
      </p>
    </>
  );
}
