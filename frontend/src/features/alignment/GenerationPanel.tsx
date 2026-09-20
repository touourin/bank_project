import { Alert, Button } from "antd";
import { Panel } from "../../ui/Panel";
import { generationPlan } from "./workflow";
import type { GraphPreview, Run } from "./types";

export function GenerationPanel({
  run,
  dirty,
  busy,
  configured,
  graph,
  onGenerate,
}: {
  run: Run;
  dirty: boolean;
  busy: boolean;
  configured: boolean;
  graph?: GraphPreview | null;
  onGenerate: () => void;
}) {
  const result = run.result!;
  const plan = generationPlan(result);
  const published = run.graph_status === "ready";
  const current = graph?.summary?.run_id === run.id ? graph.summary : null;
  const reason = dirty
    ? "生成规则有未保存修改，请先确认或撤销。"
    : !plan.included.length
      ? "暂无可生成的表，请先核对上方匹配结果。"
      : result.template && !result.template.confirmed
        ? "请先核对并确认上方的生成规则。"
        : !configured
          ? "生成图谱需要配置业务 Neo4j。"
          : "";
  return (
    <Panel
      title="生成图谱"
      eyebrow="发布结果"
      padded
      description="按确认后的规则分批读取数据，写入业务图谱。"
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
          description={plan.excluded.map((t) => t.table_name).join("、")}
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
          disabled={busy || published || Boolean(reason)}
          onClick={onGenerate}
        >
          {published ? "该版本已生成图谱" : "生成图谱"}
        </Button>
        <span className="hint">
          新图生成成功后切换当前版本；生成失败时保留原图。
        </span>
      </div>
    </Panel>
  );
}
