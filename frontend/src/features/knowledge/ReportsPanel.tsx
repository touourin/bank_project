import { useCallback, useState } from "react";
import { Input, Table } from "antd";
import { useResource } from "../../hooks/useResource";
import { ErrorNotice, LoadingState } from "../../ui/Feedback";
import { knowledgeApi } from "./api";
import { JsonDetails, pretty } from "./shared";
export function ReportsPanel({
  token,
  datasetKey,
}: {
  token: string;
  datasetKey: string;
}) {
  const resource = useResource(
    useCallback(
      (signal: AbortSignal) => knowledgeApi.reports(token, datasetKey, signal),
      [token, datasetKey],
    ),
  );
  const [search, setSearch] = useState("");
  if (resource.error)
    return <ErrorNotice message={resource.error} onRetry={resource.refresh} />;
  if (!resource.data) return <LoadingState />;
  return (
    <>
      <Input.Search
        placeholder="搜索社区报告"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <Table
        rowKey={(r) => String(r.id)}
        dataSource={resource.data.reports.filter((r) =>
          pretty(r).includes(search),
        )}
        pagination={{ pageSize: 8 }}
        columns={[
          { title: "社区", dataIndex: "community" },
          { title: "层级", dataIndex: "level" },
          { title: "报告", dataIndex: "title" },
          { title: "重要程度", dataIndex: "rank" },
        ]}
        expandable={{
          expandedRowRender: (r) => (
            <>
              <p className="knowledge-answer">
                {String(r.full_content ?? r.summary ?? "")}
              </p>
              <JsonDetails value={r} label="完整报告与发现" />
            </>
          ),
        }}
      />
      <JsonDetails value={resource.data.communities} label="社区层级与成员" />
    </>
  );
}
