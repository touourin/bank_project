import { useState } from "react";
import { Alert, Button, Input, Select, Space } from "antd";
import { ErrorNotice } from "../../ui/Feedback";
import { errorMessage } from "../../api/request";
import { alignmentApi } from "./api";
import { tableLabel } from "./workflow";
import type { Run, GraphTemplate, TemplateNode } from "./types";

export function AdvancedTemplateEditor({
  run,
  token,
  draft,
  disabled,
  update,
}: {
  run: Run;
  token: string;
  draft: GraphTemplate;
  disabled: boolean;
  update: (value: Partial<GraphTemplate>) => void;
}) {
  const [error, setError] = useState("");
  const [concepts, setConcepts] = useState<{ value: string; label: string }[]>(
    [],
  );
  const tables = run.result?.tables ?? [];
  const updateNode = (id: string, value: Partial<TemplateNode>) =>
    update({
      nodes: draft.nodes.map((n) => (n.id === id ? { ...n, ...value } : n)),
    });
  async function search(value: string) {
    try {
      const items = await alignmentApi.concepts(
        token,
        run.id,
        value,
        new AbortController().signal,
      );
      setConcepts(
        items.map((c) => ({ value: c.id, label: `${c.name} · ${c.id}` })),
      );
      setError("");
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  return (
    <>
      <Alert
        type="info"
        showIcon
        title="跨表合并需要相同的身份范围、本体概念和标识值。"
        description="请使用可靠编号；同名属性值冲突会停止发布。关系需要来源记录或明确的连接字段作为依据。"
      />
      <div className="template-nodes">
        {draft.nodes.map((node, index) => {
          const table = tables.find((t) => t.table_id === node.table_id);
          const columns =
            table?.columns.map((c) => ({ value: c.column, label: c.column })) ??
            [];
          return (
            <div className="template-node" key={node.id}>
              <Space wrap>
                <strong>节点 {index + 1}</strong>
                <Button
                  size="small"
                  disabled={disabled}
                  onClick={() =>
                    update({
                      nodes: draft.nodes.filter((n) => n.id !== node.id),
                      edges: draft.edges.filter(
                        (e) => e.source !== node.id && e.target !== node.id,
                      ),
                    })
                  }
                >
                  移除节点
                </Button>
              </Space>
              <label>
                来源表
                <Select
                  aria-label={`节点 ${index + 1} 来源表`}
                  value={node.table_id}
                  disabled={disabled}
                  options={tables.map((t) => ({
                    value: t.table_id,
                    label: tableLabel(t),
                  }))}
                  onChange={(value) =>
                    updateNode(node.id, {
                      table_id: value,
                      key_columns: [],
                      properties: [],
                    })
                  }
                />
              </label>
              <label>
                本体概念
                <Select
                  aria-label={`节点 ${index + 1} 本体概念`}
                  showSearch={{ onSearch: search, filterOption: false }}
                  onOpenChange={(open) => {
                    if (open) void search("");
                  }}
                  value={node.concept_id}
                  disabled={disabled}
                  options={[
                    {
                      value: node.concept_id,
                      label: node.concept_name || node.concept_id,
                    },
                    ...concepts.filter((c) => c.value !== node.concept_id),
                  ]}
                  onChange={(value, option) =>
                    updateNode(node.id, {
                      concept_id: value,
                      concept_name: !Array.isArray(option)
                        ? (option?.label?.split(" · ")[0] ?? value)
                        : value,
                    })
                  }
                />
              </label>
              <label>
                身份范围
                <Input
                  aria-label={`节点 ${index + 1} 身份范围`}
                  value={node.identity_scope}
                  disabled={disabled}
                  onChange={(e) =>
                    updateNode(node.id, { identity_scope: e.target.value })
                  }
                />
              </label>
              <label>
                身份标识字段
                <Select
                  aria-label={`节点 ${index + 1} 身份标识字段`}
                  mode="multiple"
                  value={node.key_columns}
                  options={columns}
                  disabled={disabled}
                  onChange={(value) =>
                    updateNode(node.id, { key_columns: value })
                  }
                />
              </label>
              {!node.key_columns.length && (
                <p className="hint">
                  未选择身份字段：每条来源记录单独生成实例。
                </p>
              )}
              <label>
                写入该节点的字段
                <Select
                  aria-label={`节点 ${index + 1} 属性字段`}
                  mode="multiple"
                  maxTagCount={6}
                  options={columns}
                  value={node.properties.map((p) => p.column)}
                  disabled={disabled}
                  onChange={(value) =>
                    updateNode(node.id, {
                      properties: value.map(
                        (column) =>
                          node.properties.find((p) => p.column === column) ?? {
                            column,
                            name: column,
                          },
                      ),
                    })
                  }
                />
              </label>
              <details>
                <summary>
                  属性名称映射（跨表补充同一属性时保持名称一致）
                </summary>
                {node.properties.map((p) => (
                  <label key={p.column}>
                    {p.column}
                    <Input
                      aria-label={`${node.id} ${p.column} 属性名称`}
                      value={p.name}
                      disabled={disabled}
                      onChange={(e) =>
                        updateNode(node.id, {
                          properties: node.properties.map((v) =>
                            v.column === p.column
                              ? { ...v, name: e.target.value }
                              : v,
                          ),
                        })
                      }
                    />
                  </label>
                ))}
              </details>
            </div>
          );
        })}
      </div>
      <Button
        disabled={disabled || !tables.some((t) => t.concept_id)}
        onClick={() => {
          const table = tables.find((t) => t.concept_id)!;
          const id = crypto.randomUUID();
          update({
            nodes: [
              ...draft.nodes,
              {
                id,
                table_id: table.table_id,
                concept_id: table.concept_id!,
                concept_name: table.concept_name ?? "",
                identity_scope: id,
                key_columns: [],
                properties: [],
              },
            ],
          });
        }}
      >
        添加实体或事件节点
      </Button>
      <div className="template-edges">
        {draft.edges.map((edge, index) => {
          const left = draft.nodes.find((n) => n.id === edge.source),
            right = draft.nodes.find((n) => n.id === edge.target);
          const edit = (value: Partial<typeof edge>) =>
            update({
              edges: draft.edges.map((e) =>
                e.id === edge.id ? { ...e, ...value } : e,
              ),
            });
          const options = draft.nodes.map((n, i) => ({
            value: n.id,
            label: `节点 ${i + 1} · ${n.concept_name}`,
          }));
          return (
            <div className="template-node" key={edge.id}>
              <strong>关系 {index + 1}</strong>
              <label>
                起点
                <Select
                  value={edge.source}
                  options={options}
                  disabled={disabled}
                  onChange={(value) => edit({ source: value })}
                />
              </label>
              <label>
                终点
                <Select
                  value={edge.target}
                  options={options}
                  disabled={disabled}
                  onChange={(value) => edit({ target: value })}
                />
              </label>
              <label>
                关系名称
                <Input
                  value={edge.name}
                  disabled={disabled}
                  onChange={(e) => edit({ name: e.target.value })}
                />
              </label>
              <label>
                数据依据
                <Select
                  value={edge.mode}
                  disabled={disabled}
                  options={[
                    { value: "same_row", label: "同一条来源记录" },
                    { value: "join", label: "连接字段相等" },
                  ]}
                  onChange={(value) => edit({ mode: value })}
                />
              </label>
              {edge.mode === "join" && (
                <>
                  <label>
                    起点连接字段
                    <Select
                      mode="multiple"
                      disabled={disabled}
                      value={edge.source_columns}
                      options={tables
                        .find((t) => t.table_id === left?.table_id)
                        ?.columns.map((c) => ({
                          value: c.column,
                          label: c.column,
                        }))}
                      onChange={(value) => edit({ source_columns: value })}
                    />
                  </label>
                  <label>
                    终点连接字段
                    <Select
                      mode="multiple"
                      disabled={disabled}
                      value={edge.target_columns}
                      options={tables
                        .find((t) => t.table_id === right?.table_id)
                        ?.columns.map((c) => ({
                          value: c.column,
                          label: c.column,
                        }))}
                      onChange={(value) => edit({ target_columns: value })}
                    />
                  </label>
                </>
              )}
              <label>
                关系依据说明
                <Input
                  value={edge.reason}
                  disabled={disabled}
                  onChange={(e) => edit({ reason: e.target.value })}
                />
              </label>
              <Button
                size="small"
                disabled={disabled}
                onClick={() =>
                  update({ edges: draft.edges.filter((e) => e.id !== edge.id) })
                }
              >
                移除关系
              </Button>
            </div>
          );
        })}
      </div>
      <Space wrap>
        <Button
          disabled={disabled || draft.nodes.length < 2}
          onClick={() =>
            update({
              edges: [
                ...draft.edges,
                {
                  id: crypto.randomUUID(),
                  source: draft.nodes[0].id,
                  target: draft.nodes[1].id,
                  name: "关联",
                  mode:
                    draft.nodes[0].table_id === draft.nodes[1].table_id
                      ? "same_row"
                      : "join",
                  source_columns: [],
                  target_columns: [],
                  reason: "",
                },
              ],
            })
          }
        >
          添加关系
        </Button>
      </Space>
      {error && <ErrorNotice message={error} />}
    </>
  );
}
