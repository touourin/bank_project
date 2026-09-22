import { useCallback, useEffect, useState } from "react";
import { Button, Select, Tag, Tabs } from "antd";
import { isDemoMode } from "../../demo";
import { OntologyBrowser } from "../ontology/OntologyBrowser";
import { useResource } from "../../hooks/useResource";
import { WorkspaceHeading } from "../../ui/WorkspaceHeading";
import { Panel } from "../../ui/Panel";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { knowledgeApi } from "../knowledge/api";
import { graphSourceKey, graphSourceLabel } from "../knowledge/graphSources";
import type { GraphSourceRef } from "../knowledge/types";
import { GraphResults } from "./GraphResults";
import "../knowledge/knowledge.css";
import "./analysis.css";

type Props = {
  token: string;
  active: boolean;
  selected?: GraphSourceRef;
  onSelect: (source: GraphSourceRef) => void;
};

export function AnalysisPage(props: Props) {
  const [mode, setMode] = useState("business");
  useEffect(() => {
    if (props.selected) setMode("business");
  }, [props.selected]);
  if (isDemoMode) return <BusinessAnalysis {...props} />;
  return (
    <Tabs
      className="analysis-mode-tabs"
      activeKey={mode}
      onChange={setMode}
      items={[
        {
          key: "business",
          label: "业务图谱",
          children: (
            <BusinessAnalysis
              {...props}
              active={props.active && mode === "business"}
            />
          ),
        },
        {
          key: "ontology",
          label: "BFO 本体",
          children: (
            <OntologyBrowser
              token={props.token}
              active={props.active && mode === "ontology"}
            />
          ),
        },
      ]}
    />
  );
}

function BusinessAnalysis({ token, active, selected, onSelect }: Props) {
  const sources = useResource(
    useCallback(
      (signal: AbortSignal) => knowledgeApi.sources(token, signal),
      [token],
    ),
    true,
  );
  const { refresh } = sources;
  useEffect(() => {
    if (active) refresh();
  }, [active, refresh]);
  const available =
    sources.data?.filter((source) => source.id && !source.error) ?? [];
  const source = selected
    ? available.find(
        (item) => graphSourceKey(item) === graphSourceKey(selected),
      )
    : available[0];
  // Keep the chosen version fixed when new conversion or review results appear.
  useEffect(() => {
    if (!selected && source) onSelect(source);
  }, [selected, source, onSelect]);
  return (
    <main className="workspace-page analysis-page">
      <WorkspaceHeading
        step="04"
        title="图谱浏览与分析"
        description="统一查看表格和文本图谱，追溯来源，并对已建立索引的文档进行问答。"
      />
      <Panel
        title="选择图谱版本"
        description="原始图谱、本体匹配和消歧结果分别保留，选择需要查看的版本。"
        actions={<Button onClick={refresh}>刷新图谱列表</Button>}
        padded
      >
        {sources.error && (
          <ErrorNotice message={sources.error} onRetry={refresh} />
        )}
        {sources.loading && !sources.data && <LoadingState />}
        {sources.data
          ?.filter((item) => item.error)
          .map((item) => (
            <ErrorNotice key={graphSourceKey(item)} message={item.error!} />
          ))}
        {available.length > 0 && (
          <>
            <Select
              className="analysis-source-picker"
              aria-label="分析图谱版本"
              showSearch
              optionFilterProp="label"
              value={source ? graphSourceKey(source) : undefined}
              placeholder="选择图谱版本"
              options={available.map((item) => ({
                value: graphSourceKey(item),
                label: `${graphSourceLabel(item)} · ${item.name}`,
              }))}
              onChange={(value) => {
                const next = available.find(
                  (item) => graphSourceKey(item) === value,
                );
                if (next) onSelect(next);
              }}
            />
            {source && (
              <p className="hint">
                <Tag>{graphSourceLabel(source)}</Tag>版本：{source.id}
              </p>
            )}
          </>
        )}
        {!sources.loading && !sources.error && !available.length && (
          <EmptyState
            title="暂无可分析图谱"
            description="请先在第二步完成数据转换，再到这里查看结果。"
          />
        )}
        {selected &&
          !source &&
          !sources.loading &&
          !sources.error &&
          available.length > 0 && (
            <ErrorNotice message="所选版本已更新或暂不可用，请重新选择图谱版本。" />
          )}
      </Panel>
      {source && (
        <div className="analysis-results">
          <GraphResults
            key={graphSourceKey(source)}
            token={token}
            source={source}
            active={active}
            onSelect={onSelect}
          />
        </div>
      )}
    </main>
  );
}
