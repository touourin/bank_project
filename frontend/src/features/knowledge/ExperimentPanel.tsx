import { useCallback, useState } from "react";
import { Alert, Button, Input, Select, Table, Tabs } from "antd";
import { useResource } from "../../hooks/useResource";
import { EmptyState, ErrorNotice, LoadingState } from "../../ui/Feedback";
import { downloadExperiment, knowledgeApi } from "./api";
import { JsonDetails, pretty } from "./shared";
export function ExperimentPanel({ token }: { token: string }) {
  const list = useResource(
    useCallback(
      (signal: AbortSignal) => knowledgeApi.experiments(token, signal),
      [token],
    ),
  );
  const [selected, setSelected] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const current =
    selected || list.data?.runs.find((r) => r.state === "complete")?.name || "";
  const detail = useResource(
    useCallback(
      (signal: AbortSignal) =>
        current
          ? knowledgeApi.experiment(token, current, signal)
          : Promise.resolve(null),
      [token, current],
    ),
  );
  const value = detail.data;
  return (
    <div className="knowledge-experiments">
      <h2>原消歧实验对比</h2>
      <p className="hint">
        按原项目清单校验输入指纹、产物哈希及全量成员覆盖后展示。线上分析自动保存实验；历史离线实验可放入配置的实验目录。
      </p>
      <div className="knowledge-toolbar">
        <Select
          aria-label="选择消歧实验"
          style={{ minWidth: 300 }}
          value={current || undefined}
          onChange={setSelected}
          options={list.data?.runs.map((r) => ({
            value: r.name,
            label: `${r.created_at.slice(0, 19)} · ${r.name} · ${r.state}`,
            disabled: r.state !== "complete",
          }))}
        />
        <Button onClick={list.refresh}>刷新实验</Button>
      </div>
      {(list.error || detail.error || error) && (
        <ErrorNotice message={list.error || detail.error || error} />
      )}
      {list.data?.errors.map((e) => (
        <Alert key={e} type="warning" title={e} />
      ))}
      {detail.loading && <LoadingState />}
      {!current && !list.loading && (
        <EmptyState
          title="尚无完整实验"
          description="启动一次候选分析后，此处会展示原方法对比及可下载报告。"
        />
      )}
      {value && (
        <>
          <Alert
            type="info"
            title={
              value.report.evaluation_status === "MEASURED_ON_PROVIDED_GOLD"
                ? "已在提供的人工金标上评分"
                : "无人工金标：只比较分组变化，不推断准确率"
            }
          />
          <p>
            {value.report.records} 条记录 · {value.report.changed_records}{" "}
            条归属变化 ·{" "}
            {Object.entries(value.report.entity_counts)
              .map(([m, n]) => `${m}: ${n} 个实体`)
              .join(" / ")}
          </p>
          <Button
            onClick={() =>
              void downloadExperiment(token, current).catch((e) =>
                setError(e.message),
              )
            }
          >
            下载完整报告与标注模板
          </Button>
          <Tabs
            items={[
              {
                key: "partitions",
                label: "记录与分组对比",
                children: (
                  <>
                    <Input.Search
                      placeholder="搜索名称、记录或来源"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                    <Table
                      size="small"
                      rowKey={(r) => String(r["记录 ID"])}
                      dataSource={value.records.filter((r) =>
                        pretty(r).includes(search),
                      )}
                      scroll={{ x: 1000 }}
                      pagination={{ pageSize: 10 }}
                      columns={[
                        "名称",
                        "类型",
                        "归属变化",
                        "旧簇大小",
                        "新簇大小",
                        "旧状态",
                        "新状态",
                      ].map((key) => ({ title: key, dataIndex: key }))}
                      expandable={{
                        expandedRowRender: (row) => {
                          const mid = String(row["记录 ID"]);
                          return (
                            <>
                              <JsonDetails
                                value={value.corpus.mentions.find(
                                  (m) => m.mention_id === mid,
                                )}
                                label="原记录与来源证据"
                              />
                              {value.report.methods.map((method) => {
                                const result = value.results[method];
                                const eid = result.memberships.find(
                                  (m) => m.mention_id === mid,
                                )?.entity_id;
                                const entity = result.entities.find(
                                  (e) => e.entity_id === eid,
                                );
                                return (
                                  <div key={method}>
                                    <h4>{method}</h4>
                                    <p>
                                      {entity?.mention_ids
                                        .map(
                                          (id) =>
                                            value.corpus.mentions.find(
                                              (m) => m.mention_id === id,
                                            )?.name ?? id,
                                        )
                                        .join("、") ?? "无归属"}
                                    </p>
                                    <JsonDetails
                                      value={entity}
                                      label="实体簇完整信息"
                                    />
                                    <JsonDetails
                                      value={result.decisions.filter(
                                        (d) =>
                                          d.left === mid || d.right === mid,
                                      )}
                                      label="候选判决、证据与拒绝原因"
                                    />
                                  </div>
                                );
                              })}
                            </>
                          );
                        },
                      }}
                    />
                  </>
                ),
              },
              {
                key: "metrics",
                label: "评估指标",
                children: (
                  <>
                    <MetricsTable metrics={value.report.metrics} />
                    <JsonDetails
                      value={value.report}
                      label="指标、覆盖率与预算统计"
                    />
                  </>
                ),
              },
              {
                key: "synonyms",
                label: "名称同义词",
                children: (
                  <>
                    <JsonDetails
                      value={value.aliases}
                      label="逐条别名及原文引用"
                    />
                    <JsonDetails value={value.synonyms} label="同义词提案" />
                  </>
                ),
              },
              {
                key: "diff",
                label: "归属差异",
                children: (
                  <JsonDetails value={value.differences} label="逐条差异" />
                ),
              },
              {
                key: "info",
                label: "实验信息",
                children: (
                  <>
                    <JsonDetails
                      value={value.manifest}
                      label="来源与产物哈希"
                    />
                    <JsonDetails value={value.config} label="本次消歧配置" />
                    <JsonDetails value={value.gold} label="人工金标" />
                  </>
                ),
              },
            ]}
          />
        </>
      )}
    </div>
  );
}

function MetricsTable({ metrics }: { metrics: unknown }) {
  if (!metrics || typeof metrics !== "object")
    return <p>尚未提供金标，指标未测量。</p>;
  const methods = Object.keys(metrics);
  const rows = new Map<string, Record<string, string>>();
  function visit(value: unknown, path: string, method: string) {
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      !("value" in value)
    ) {
      for (const [k, v] of Object.entries(value))
        visit(v, path ? `${path} / ${k}` : k, method);
      return;
    }
    const row = rows.get(path) ?? { metric: path };
    row[method] =
      value && typeof value === "object" && "value" in value
        ? pretty(value)
        : value === null
          ? "N/A"
          : String(value);
    rows.set(path, row);
  }
  for (const [method, value] of Object.entries(metrics))
    visit(value, "", method);
  return (
    <Table
      rowKey="metric"
      size="small"
      pagination={false}
      dataSource={[...rows.values()]}
      columns={[
        { title: "指标", dataIndex: "metric" },
        ...methods.map((m) => ({ title: m, dataIndex: m })),
      ]}
    />
  );
}
