import { Collapse, Input, InputNumber, Select } from "antd";
import type { ResolutionOptions } from "./types";
export const defaultResolutionOptions: ResolutionOptions = {
  method: "evidence_v1",
  retrieval_policy: "balanced",
  candidate_limit: 10,
  model_policy: "review",
  max_model_calls: 2000,
  max_alias_calls: 200,
  concurrency: 8,
  timeout_seconds: 60,
};
export function ResolutionOptionsPanel({
  value,
  onChange,
  onError,
}: {
  value: ResolutionOptions;
  onChange: (v: ResolutionOptions) => void;
  onError: (s: string) => void;
}) {
  const set = (key: string, v: unknown) => onChange({ ...value, [key]: v });
  async function file(key: string, f: File | undefined) {
    if (!f) {
      set(key, undefined);
      return;
    }
    try {
      if (f.size > 10 * 1024 * 1024) throw new Error("JSON 文件不能超过 10 MB");
      set(key, JSON.parse(await f.text()));
      onError("");
    } catch (e) {
      onError(e instanceof Error ? e.message : "JSON 无效");
    }
  }
  return (
    <Collapse
      items={[
        {
          key: "options",
          label: "消歧方法与参数",
          children: (
            <>
              <label className="knowledge-field">
                消歧方法
                <Select
                  aria-label="消歧方法"
                  value={value.method}
                  onChange={(v) =>
                    onChange({
                      ...value,
                      method: v,
                      ...(v === "evidence_v1" ? { synonyms: undefined } : {}),
                    })
                  }
                  options={[
                    { value: "evidence_v1", label: "原方法 · 证据消歧" },
                    {
                      value: "synonym_llm_v1",
                      label: "原方法 · 同义词＋大模型",
                    },
                  ]}
                />
              </label>
              <label className="knowledge-field">
                候选策略
                <Select
                  aria-label="候选策略"
                  value={value.retrieval_policy ?? "balanced"}
                  onChange={(v) =>
                    onChange({
                      ...value,
                      retrieval_policy: v,
                      candidate_limit: v === "balanced" ? 10 : 30,
                    })
                  }
                  options={[
                    { value: "balanced", label: "精筛候选 · 身份线索优先" },
                    { value: "original", label: "原始宽召回 · 更多候选" },
                  ]}
                />
              </label>
              <p className="hint">
                精筛候选减少常见描述词和弱类型匹配造成的比较，名称、别名与编号线索优先。未召回不代表确认不同，可切回宽召回或人工指定节点。
              </p>
              <label className="knowledge-field">
                合并策略
                <Select
                  aria-label="合并策略"
                  value={value.model_policy}
                  onChange={(v) => set("model_policy", v)}
                  options={[
                    { value: "review", label: "人工校验后合并" },
                    { value: "apply", label: "应用引擎确认结果，可撤销" },
                  ]}
                />
              </label>
              {(
                [
                  ["max_model_calls", "判定调用预算", 1, 20000],
                  ["max_alias_calls", "别名调用预算", 0, 20000],
                  ["concurrency", "模型并发", 1, 32],
                  ["timeout_seconds", "单次判定超时（秒）", 1, 600],
                  ["candidate_limit", "每条记录候选上限", 1, 500],
                  ["max_pairs", "候选对上限", 1, 100000],
                  ["max_block_size", "候选分组上限", 1, 5000],
                  ["max_vector_records", "向量记录上限", 1, 20000],
                  ["max_cluster_size", "单簇记录上限", 1, 100],
                  ["embedding_neighbors", "向量邻居数", 1, 100],
                ] as const
              ).map(([key, label, min, max]) => (
                <label className="knowledge-field" key={key}>
                  {label}
                  <InputNumber
                    aria-label={label}
                    min={min}
                    max={max}
                    value={Number(
                      value[key] ??
                        (
                          {
                            candidate_limit: 10,
                            max_pairs: 20000,
                            max_block_size: 500,
                            max_vector_records: 2000,
                            max_cluster_size: 100,
                            embedding_neighbors: 10,
                          } as Record<string, number>
                        )[key],
                    )}
                    onChange={(v) => set(key, v ?? min)}
                  />
                </label>
              ))}
              <label className="knowledge-field">
                唯一身份标识命名空间
                <Input
                  placeholder="逗号分隔"
                  onChange={(e) =>
                    set(
                      "unique_id_namespaces",
                      e.target.value
                        .split(",")
                        .map((s) => s.trim())
                        .filter(Boolean),
                    )
                  }
                />
              </label>
              {[
                ["synonyms", "人工审校同义词词表（JSON）"],
                ["vectors", "向量召回数据（JSON）"],
                ["gold", "人工金标（仅评分，JSON）"],
              ].map(([key, label]) => (
                <label className="knowledge-field" key={key}>
                  {label}
                  <input
                    type="file"
                    accept=".json,application/json"
                    disabled={
                      key === "synonyms" && value.method !== "synonym_llm_v1"
                    }
                    onChange={(e) => void file(key, e.target.files?.[0])}
                  />
                  {value[key] != null && <small>已载入</small>}
                </label>
              ))}
              <p className="hint">
                每次分析保存同输入的旧方法与新方法对比。词表仅供同义词方法使用；向量用于召回，金标不参与判定。预算不足和模型失败会在进度与报告中披露。
              </p>
            </>
          ),
        },
      ]}
    />
  );
}
