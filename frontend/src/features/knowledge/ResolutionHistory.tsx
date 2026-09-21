import { Button } from "antd";
import { EmptyState } from "../../ui/Feedback";
import { AuditTable } from "./shared";
import type {
  KnowledgeNode,
  ResolutionCandidate,
  ResolutionRun,
} from "./types";

export function ResolutionHistory({
  run,
  disabled,
  onRollback,
}: {
  run: ResolutionRun;
  disabled: boolean;
  onRollback: (candidate: ResolutionCandidate) => void;
}) {
  const candidates = new Map(
    run.candidates.map((candidate) => [candidate.id, candidate]),
  );
  const latestAudits = new Map(
    run.audits.map((audit) => [audit.candidate_id, audit]),
  );
  return (
    <div className="resolution-history">
      <section aria-label="当前生效的合并">
        <p className="hint">
          当前有 {run.merges.length} 次合并生效，可在对应记录中退回并重新核验。
        </p>
        {run.merges.length ? (
          run.merges.map((merge) => {
            const candidate = candidates.get(merge.candidate_id);
            const audit = latestAudits.get(merge.candidate_id);
            return (
              <article
                key={merge.candidate_id}
                className="resolution-candidate"
              >
                <div className="knowledge-toolbar">
                  <h3>已合并 {merge.source_nodes.length} 个实体</h3>
                  <Button
                    danger
                    disabled={
                      disabled || !candidate || candidate.status !== "merged"
                    }
                    onClick={() => candidate && onRollback(candidate)}
                  >
                    退回合并
                  </Button>
                </div>
                {audit && (
                  <p className="hint">
                    {audit.action === "manual" ? "人工指定合并" : "确认合并"}
                    {" · "}
                    {audit.reviewer || "未填写审核人"}
                    {" · "}
                    {new Date(audit.created_at).toLocaleString()}
                    {" · 修订 "}
                    {audit.revision}
                  </p>
                )}
                {audit?.note && (
                  <p className="resolution-review-note">
                    审核备注：{audit.note}
                  </p>
                )}
                {candidate &&
                  merge.source_nodes.length > candidate.nodes.length && (
                    <p className="hint">
                      本次合并决定：
                      {candidate.nodes.map((node) => node.name).join(" / ")}。
                      下方展示包含其他合并决定的当前实体组。
                    </p>
                  )}
                <MergeFlow
                  nodes={merge.source_nodes}
                  target={merge.target_node}
                />
              </article>
            );
          })
        ) : (
          <EmptyState
            title="暂无生效中的合并"
            description="合并和退回操作可在下方审核记录中查看。"
          />
        )}
      </section>
      <section aria-label="审核记录">
        <h3>审核记录（{run.audits.length}）</h3>
        <AuditTable audits={[...run.audits].reverse()} showEntities />
      </section>
    </div>
  );
}

type BriefNode = Pick<KnowledgeNode, "id" | "name" | "type">;
export function MergeFlow({
  nodes,
  target,
}: {
  nodes: BriefNode[];
  target?: BriefNode;
}) {
  return (
    <div
      className="merge-flow"
      aria-label={`${nodes.length} 个节点合并为 1 个节点`}
    >
      <div className="merge-flow-sources">
        {nodes.map((node) => (
          <div className="merge-node" key={node.id}>
            <strong>{node.name}</strong>
            <small>
              {node.type} · {node.id}
            </small>
          </div>
        ))}
      </div>
      <span aria-hidden="true">→</span>
      <div className="merge-node merge-node-target">
        <strong>{target?.name || "选择保留节点"}</strong>
        <small>
          {target ? `${target.type} · ${target.id}` : "合并后的唯一实体"}
        </small>
      </div>
    </div>
  );
}
