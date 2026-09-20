import { useCallback, useState } from "react";
import { Button, Input, Modal } from "antd";
import { useResource } from "../../hooks/useResource";
import { ErrorNotice, LoadingState } from "../../ui/Feedback";
import { alignmentApi } from "./api";
import type { GraphNodeBrief } from "./types";

export function GraphNodeDetails({
  token,
  version,
  node,
  onClose,
  onNeighbors,
}: {
  token: string;
  version: string;
  node: GraphNodeBrief;
  onClose: () => void;
  onNeighbors: () => void;
}) {
  const detail = useResource(
    useCallback(
      (signal: AbortSignal) =>
        alignmentApi.graphNode(token, version, node.id, signal),
      [token, version, node.id],
    ),
  );
  const [query, setQuery] = useState("");
  const fields = Object.entries(detail.data?.fields ?? {});
  const filtered = fields.filter(([key, value]) =>
    `${key}\n${value ?? ""}`.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <Modal
      open
      title={detail.data?.name || node.name}
      onCancel={onClose}
      width={720}
      footer={<Button onClick={onNeighbors}>查看此实例的业务关联</Button>}
    >
      <p className="hint">
        {node.concept_name} · {node.table_name} · 来源第 {node.source_row} 行
      </p>
      <p className="hint">
        本体节点：<code>{node.concept_id}</code>
      </p>
      <p className="hint">
        实例标识：<code>{node.id}</code>
      </p>
      {detail.loading ? (
        <LoadingState label="读取实例全部属性…" />
      ) : detail.error ? (
        <ErrorNotice message={detail.error} onRetry={detail.refresh} />
      ) : (
        <>
          <Input.Search
            aria-label="筛选实例属性"
            placeholder="查找字段或值"
            allowClear
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <p className="hint">
            显示 {filtered.length} / {fields.length}{" "}
            个已写入属性。历史图谱中的空值可能未写入，原始记录保留在数据接入中。
          </p>
          <dl className="node-fields">
            {filtered.map(([name, value]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>
                  {value === null ? (
                    <span className="hint">NULL</span>
                  ) : value === "" ? (
                    <span className="hint">空字符串</span>
                  ) : (
                    value
                  )}
                </dd>
              </div>
            ))}
          </dl>
        </>
      )}
    </Modal>
  );
}
