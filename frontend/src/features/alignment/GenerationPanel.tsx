import { Alert, Button } from "antd";
import { Panel } from "../../ui/Panel";
import { generationPlan, tableLabel, exclusionReason } from "./workflow";
import type { GraphOverview, MappingResult, Run } from "./types";

export function GenerationPanel({
  run,
  result,
  dirty,
  busy,
  configured,
  graph,
  onGenerate,
}: {
  run: Run;
  result: MappingResult;
  dirty: boolean;
  busy: boolean;
  configured: boolean;
  graph?: GraphOverview | null;
  onGenerate: () => void;
}) {
  const plan = generationPlan(result);
  const published = run.graph_status === "ready";
  const current = graph?.summary?.run_id === run.id ? graph.summary : null;
  const reason = !plan.included.length
    ? "暂无可生成的对象，请查看下方原因，或修改对象类型。"
    : !configured
      ? "生成图谱需要配置业务 Neo4j。"
      : "";
  return (
    <Panel
      title="生成图谱"
      eyebrow="发布结果"
      padded
      description="一次采纳整套匹配方案并分批生成；会一并保存当前修改。"
    >
      <div className="generation-metrics">
        <div>
          <strong>{plan.included.length}</strong>
          <span>张来源表</span>
        </div>
        <div>
          <strong>{plan.rows.toLocaleString()}</strong>
          <span>行来源数据</span>
        </div>
        <div>
          <strong>{plan.concepts.length}</strong>
          <span>种对象类型</span>
        </div>
        <div>
          <strong>{plan.relationRules}</strong>
          <span>条关系规则</span>
        </div>
      </div>
      <p className="generation-types">
        对象类型：{plan.concepts.join("、") || "尚未确定"}
      </p>
      <p className="hint">
        上面是输入数据和规则数量。拆分、合并后的实际节点数与关系数，在生成完成后显示。
      </p>
      {plan.relationRules === 0 && plan.included.length > 0 && (
        <p className="hint">
          本次没有关系规则，只生成实例节点，不建立节点间连线。
        </p>
      )}
      {plan.excluded.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title={`以下 ${plan.excluded.length} 张表不参与本次生成`}
          description={
            <ul>
              {plan.excluded.map((t) => (
                <li key={t.table_id}>
                  {tableLabel(t)}：{exclusionReason(t)}
                </li>
              ))}
            </ul>
          }
        />
      )}
      {plan.suggestedNodes > 0 && (
        <Alert
          type="warning"
          showIcon
          title={`包含 ${plan.suggestedNodes} 个低分或未验证的对象建议`}
          description="可整体采纳，也可先修改。采纳不会改变原始匹配分数；这些结果仍有不确定性。"
        />
      )}
      {published && (
        <Alert
          type="success"
          showIcon
          title={
            current
              ? `已生成 ${current.node_count.toLocaleString()} 个实例、${current.edge_count.toLocaleString()} 条关系`
              : "该任务版本已生成过图谱"
          }
          description={
            current
              ? "下方展示的就是这次发布的结果。"
              : "下方展示当前已发布版本，请核对其来源任务。"
          }
        />
      )}
      {reason && (!published || dirty) && (
        <p className="hint" role="status">
          {reason}
        </p>
      )}
      <div className="generation-action">
        <Button
          type="primary"
          disabled={busy || (published && !dirty) || Boolean(reason)}
          onClick={onGenerate}
        >
          {published && !dirty ? "该版本已生成图谱" : "采纳方案并生成"}
        </Button>
        <span className="hint">
          新图生成成功后切换当前版本；生成失败时保留原图。
        </span>
      </div>
    </Panel>
  );
}
