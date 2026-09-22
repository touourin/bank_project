import { useCallback, useEffect, useState } from "react";
import { Button, Select, Switch, Tag } from "antd";
import { useResource } from "../../hooks/useResource";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { JsonDetails } from "../../ui/JsonDetails";
import { Panel } from "../../ui/Panel";
import { WorkspaceHeading } from "../../ui/WorkspaceHeading";
import { KnowledgeGraphPanel } from "../knowledge/KnowledgeGraphPanel";
import { ontologyApi, type OntologyGraph } from "./api";

export function OntologyBrowser({
  token,
  active,
}: {
  token: string;
  active: boolean;
}) {
  const resource = useResource(
    useCallback(
      (signal: AbortSignal) => ontologyApi.graph(token, signal),
      [token],
    ),
    true,
  );
  const refresh = resource.refresh;
  useEffect(() => {
    if (active) refresh();
  }, [active, refresh]);
  return (
    <main className="workspace-page analysis-page">
      <WorkspaceHeading
        step="04"
        title="BFO 本体浏览"
        description="查看数据转换与风险规则共用的概念、分类关系及业务依据。"
      />
      <Panel
        title="当前本体"
        actions={<Button onClick={refresh}>刷新本体</Button>}
        padded
      >
        {resource.loading && !resource.data && (
          <LoadingState label="读取本体目录与关系…" />
        )}
        {resource.error && (
          <ErrorNotice message={resource.error} onRetry={refresh} />
        )}
        {resource.data && (
          <>
            <p>
              <Tag>
                {resource.data.source.kind === "remote"
                  ? "远端本体"
                  : "本地本体"}
              </Tag>
              {resource.data.source.node_count.toLocaleString()} 个概念 ·{" "}
              {resource.data.source.relation_count.toLocaleString()} 条关系 ·{" "}
              {resource.data.source.why_node_count.toLocaleString()} 个含 WHY
            </p>
            <p className="hint">
              本体：{resource.data.source.ontology_id || "本地目录"} · 版本：
              {resource.data.source.revision}
            </p>
            <p className="hint">
              节点代表概念，连线代表本体关系。客户、事件等实际记录在“业务图谱”中查看。
            </p>
            <ConceptDetails
              key={resource.data.source.snapshot_sha256}
              token={token}
              ontology={resource.data}
            />
            <KnowledgeGraphPanel graph={resource.data.graph} nodeLabel="概念" />
          </>
        )}
      </Panel>
    </main>
  );
}

function ConceptDetails({
  token,
  ontology,
}: {
  token: string;
  ontology: OntologyGraph;
}) {
  const [selected, setSelected] = useState("");
  const [whyOnly, setWhyOnly] = useState(false);
  const nodes = ontology.graph.nodes.filter(
    (node) => !whyOnly || node.properties.has_why,
  );
  const dimensions = useResource(
    useCallback(
      (signal: AbortSignal) =>
        selected
          ? ontologyApi.dimensions(token, ontology.source, selected, signal)
          : Promise.resolve(undefined),
      [token, ontology.source, selected],
    ),
  );
  return (
    <section aria-label="本体概念详情" className="ontology-concept-details">
      <div className="ontology-detail-controls">
        <Select
          aria-label="选择本体概念查看依据"
          showSearch
          optionFilterProp="label"
          allowClear
          placeholder="搜索概念，查看 WHAT 定义和 WHY 依据"
          value={selected || undefined}
          onChange={(value) => setSelected(value ?? "")}
          options={nodes.map((node) => ({
            value: node.id,
            label: `${node.name} · ${node.id} · ${node.type}${node.properties.has_why ? " · 有 WHY" : ""}`,
          }))}
        />
        <label>
          <Switch
            checked={whyOnly}
            onChange={(value) => {
              setWhyOnly(value);
              setSelected("");
            }}
            aria-label="只看含 WHY 的概念"
          />{" "}
          只看含 WHY
        </label>
      </div>
      {selected && dimensions.loading && <LoadingState label="读取概念依据…" />}
      {dimensions.error && (
        <ErrorNotice message={dimensions.error} onRetry={dimensions.refresh} />
      )}
      {dimensions.data && (
        <>
          {dimensions.data.what != null && (
            <JsonDetails label="WHAT · 概念定义" value={dimensions.data.what} />
          )}
          {dimensions.data.why != null ? (
            <JsonDetails label="WHY · 业务依据" value={dimensions.data.why} />
          ) : (
            <EmptyState
              title="该概念没有直接 WHY"
              description="风险生成仍会沿允许的本体关系查找适用依据。"
            />
          )}
        </>
      )}
    </section>
  );
}
