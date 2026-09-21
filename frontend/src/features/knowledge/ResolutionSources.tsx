import { useCallback, useState } from "react";
import { Alert, Button, Modal } from "antd";
import { useResource } from "../../hooks/useResource";
import { ErrorNotice, LoadingState } from "../../ui/Feedback";
import { knowledgeApi } from "./api";
import { JsonDetails, pretty } from "./shared";
import type { ResolutionSources } from "./types";

type Props = { token: string; runId: string; candidateId: string };

export function ResolutionSourcesPanel(props: Props & { available: boolean }) {
  return (
    <section className="resolution-source-panel" aria-label="原文与引用">
      {props.available ? (
        <SourceViewer {...props} />
      ) : (
        <p className="hint">分析完成后展示原文与引用</p>
      )}
    </section>
  );
}

function SourceViewer({ token, runId, candidateId }: Props) {
  const [open, setOpen] = useState(false);
  const resource = useResource(
    useCallback(
      (signal: AbortSignal) =>
        knowledgeApi.resolutionSources(token, runId, candidateId, signal),
      [token, runId, candidateId],
    ),
  );
  return (
    <>
      <div className="knowledge-toolbar">
        <h4>原文与引用</h4>
        <Button disabled={!resource.data} onClick={() => setOpen(true)}>
          展开阅读
        </Button>
      </div>
      {resource.loading && <LoadingState label="正在加载原文与引用…" />}
      {resource.error && (
        <ErrorNotice message={resource.error} onRetry={resource.refresh} />
      )}
      {resource.data && <SourceContent data={resource.data} />}
      {open && resource.data && (
        <Modal
          open
          title="消歧依据 · 原文与引用"
          width={1200}
          footer={null}
          onCancel={() => setOpen(false)}
        >
          <SourceContent data={resource.data} />
        </Modal>
      )}
    </>
  );
}

function Highlight({ text, quote }: { text: string; quote: string | null }) {
  if (!quote?.trim() || !text.includes(quote)) return <>{text}</>;
  return text.split(quote).map((part, index) => (
    <span key={index}>
      {index > 0 && <mark>{quote}</mark>}
      {part}
    </span>
  ));
}

function SourceContent({ data }: { data: ResolutionSources }) {
  const { source_name, source_kind, nodes } = data;
  const document = source_kind === "graphrag";
  return (
    <>
      <p className="hint">来源：{source_name} · 本次分析保存的来源快照</p>
      <p className="hint">
        {document
          ? "以下为节点关联的完整原文分块；多块按分析时顺序拼接。高亮仅标记模型引用在原文中逐字出现的位置。"
          : "以下为本次分析保存的数据库来源字段，不是文档原文。"}
      </p>
      <div className="resolution-sources">
        {nodes.map((node, index) => (
          <section
            className="resolution-source"
            key={node.node_id}
            aria-label={`来源节点 ${index + 1}`}
          >
            <h3>
              {index === 0
                ? "左侧"
                : index === 1
                  ? "右侧"
                  : `节点 ${index + 1}`}{" "}
              · {node.name}
            </h3>
            <p className="hint">{node.node_id}</p>
            <h4>模型引用</h4>
            <blockquote>{node.quote || "模型未提供引用"}</blockquote>
            <Alert
              showIcon
              type={
                ["source_text", "source_record"].includes(
                  node.quote_location.origin,
                )
                  ? "info"
                  : "warning"
              }
              title={node.quote_location.message}
              description={
                node.quote_location.fields?.length
                  ? `实际命中的节点属性：${node.quote_location.fields.join("、")}`
                  : undefined
              }
            />
            {node.records.map((record, recordIndex) => (
              <div
                className="resolution-source-record"
                key={`${record.node_id}:${recordIndex}`}
              >
                {node.records.length > 1 && (
                  <h4>
                    来源记录 {recordIndex + 1} · {record.name}
                  </h4>
                )}
                <h4>{document ? "文档原文" : "数据库来源字段"}</h4>
                {document ? (
                  record.source_text ? (
                    <pre
                      className="resolution-source-text"
                      aria-label={`原文 ${record.node_id}`}
                    >
                      <Highlight text={record.source_text} quote={node.quote} />
                    </pre>
                  ) : (
                    <p className="hint">
                      此记录未保存文档原文，不能用节点名称或 description 代替。
                    </p>
                  )
                ) : (
                  <pre
                    className="resolution-source-text"
                    aria-label={`来源字段 ${record.node_id}`}
                  >
                    <Highlight
                      text={pretty(record.fields)}
                      quote={node.quote}
                    />
                  </pre>
                )}
                {record.text_unit_ids.length > 0 && (
                  <JsonDetails
                    label="原文分块 ID"
                    value={record.text_unit_ids}
                  />
                )}
                {document && record.description != null && (
                  <JsonDetails
                    label="节点 description（抽取后的描述，不是原文）"
                    value={record.description}
                  />
                )}
              </div>
            ))}
          </section>
        ))}
      </div>
    </>
  );
}
