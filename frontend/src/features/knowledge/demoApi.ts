import type {
  ConceptDetail,
  Dataset,
  KnowledgeGraph,
  KnowledgeSource,
  MatchRun,
  ResolutionCandidate,
  ResolutionRun,
  ResolutionSources,
  SourceKind,
} from "./types";

// Demo data is intentionally kept in memory. No network or model calls occur here.
const copy = <T>(value: T): T => structuredClone(value);
const reply = <T>(value: T): Promise<T> => Promise.resolve(copy(value));
const now = () => new Date().toISOString();
const sourceId = "demo-bank-relations";
const title = "银行与企业关系 · 示例文本";
const baseGraph: KnowledgeGraph = {
  id: "demo-graph",
  name: title,
  source_kind: "graphrag",
  source_id: sourceId,
  nodes: [
    {
      id: "n1",
      name: "华星科技有限公司",
      type: "ORGANIZATION",
      properties: {
        description: "示例企业，提供工业软件服务",
        行业: "软件与信息服务",
        统一社会信用代码: "DEMO-HX-001",
        城市: "上海",
      },
    },
    {
      id: "n2",
      name: "华星科技",
      type: "ORGANIZATION",
      properties: {
        description: "华星科技有限公司的文本简称",
        统一社会信用代码: "DEMO-HX-001",
        城市: "上海",
      },
    },
    {
      id: "n3",
      name: "远航制造有限公司",
      type: "ORGANIZATION",
      properties: {
        description: "示例企业，采购工业软件并开展设备升级",
        行业: "高端装备制造",
        地址: "示例产业园 A 区",
      },
    },
    {
      id: "n4",
      name: "远航制造",
      type: "ORGANIZATION",
      properties: {
        description: "远航制造有限公司的简称，地址记录待核验",
        地址: "示例产业园 B 区",
      },
    },
    {
      id: "n5",
      name: "城商银行股份有限公司",
      type: "FINANCIAL_INSTITUTION",
      properties: {
        description: "虚构示例银行，为企业提供授信服务",
        机构代码: "DEMO-BANK-001",
      },
    },
    {
      id: "n6",
      name: "城商银行",
      type: "FINANCIAL_INSTITUTION",
      properties: { description: "示例银行简称", 机构代码: "DEMO-BANK-001" },
    },
    {
      id: "n7",
      name: "林明（示例）",
      type: "PERSON",
      properties: { description: "华星科技示例法定代表人", 职务: "总经理" },
    },
    {
      id: "n8",
      name: "智能产线升级项目",
      type: "PROJECT",
      properties: {
        description: "远航制造的示例技改项目",
        项目金额: "12000000.00",
        币种: "CNY",
      },
    },
    {
      id: "n9",
      name: "恒信担保有限公司",
      type: "ORGANIZATION",
      properties: { description: "为示例技改贷款提供担保", 业务: "融资担保" },
    },
    {
      id: "n10",
      name: "产业升级贷款",
      type: "FINANCIAL_PRODUCT",
      properties: {
        description: "示例企业贷款产品",
        贷款金额: "8000000.00",
        期限: "36 个月",
      },
    },
  ],
  edges: [
    {
      id: "e1",
      source: "n1",
      target: "n3",
      edge_type: "SUPPLIES",
      properties: { description: "华星科技向远航制造提供工业软件", weight: 1 },
    },
    {
      id: "e2",
      source: "n2",
      target: "n8",
      edge_type: "PARTICIPATES_IN",
      properties: { description: "华星科技参与智能产线升级项目" },
    },
    {
      id: "e3",
      source: "n5",
      target: "n3",
      edge_type: "LENDS_TO",
      properties: {
        description: "城商银行向远航制造提供示例授信",
        金额: "8000000.00",
      },
    },
    {
      id: "e4",
      source: "n6",
      target: "n10",
      edge_type: "PROVIDES",
      properties: { description: "城商银行提供产业升级贷款" },
    },
    {
      id: "e5",
      source: "n7",
      target: "n1",
      edge_type: "REPRESENTS",
      properties: { description: "林明为华星科技法定代表人" },
    },
    {
      id: "e6",
      source: "n3",
      target: "n8",
      edge_type: "OWNS",
      properties: { description: "远航制造负责智能产线升级项目" },
    },
    {
      id: "e7",
      source: "n9",
      target: "n4",
      edge_type: "GUARANTEES",
      properties: { description: "恒信担保为远航制造提供融资担保" },
    },
    {
      id: "e8",
      source: "n10",
      target: "n8",
      edge_type: "FINANCES",
      properties: { description: "贷款用于智能产线升级项目" },
    },
    {
      id: "e9",
      source: "n5",
      target: "n1",
      edge_type: "SERVES",
      properties: { description: "华星科技为城商银行对公客户" },
    },
  ],
};
const candidatePairs: [string, string][] = [
  ["n1", "n2"],
  ["n3", "n4"],
  ["n5", "n6"],
];
const datasets: Dataset[] = [
  {
    key: sourceId,
    name: title,
    status: "succeeded",
    stage: "示例图谱已就绪",
    progress: 1,
    error: null,
  },
];
const concepts: ConceptDetail[] = [
  {
    id: "BO_CORP",
    name: "对公客户",
    score: 0.98,
    parents: [{ id: "BO_ORG", name: "组织" }],
  },
  {
    id: "BO_BANK",
    name: "银行机构",
    score: 0.96,
    parents: [{ id: "BO_ORG", name: "组织" }],
  },
  { id: "BO_PERSON", name: "自然人", score: 0.97, parents: [] },
  { id: "BO_PROJECT", name: "融资项目", score: 0.94, parents: [] },
  { id: "BO_LOAN", name: "贷款产品", score: 0.93, parents: [] },
  { id: "BO_ORG", name: "组织", score: 0.72, parents: [] },
];
const resolutionRuns: ResolutionRun[] = [];
const resolutionInputs = new Map<string, KnowledgeGraph>();
const matchRuns: MatchRun[] = [];
const matchInputs = new Map<string, KnowledgeGraph>();
let serial = 0;
function resolutionGraph(run: ResolutionRun): KnowledgeGraph {
  const graph = copy(resolutionInputs.get(run.id) ?? baseGraph);
  const redirects = new Map<string, string>();
  for (const candidate of run.candidates) {
    if (candidate.status === "merged" && candidate.canonical_id) {
      for (const id of candidate.node_ids)
        if (id !== candidate.canonical_id)
          redirects.set(id, candidate.canonical_id);
    }
  }
  const canonical = (id: string): string => {
    const visited = new Set<string>();
    while (redirects.has(id) && !visited.has(id)) {
      visited.add(id);
      id = redirects.get(id)!;
    }
    return id;
  };
  graph.nodes = graph.nodes.filter((node) => !redirects.has(node.id));
  graph.edges = graph.edges
    .map((edge) => ({
      ...edge,
      source: canonical(edge.source),
      target: canonical(edge.target),
    }))
    .filter((edge) => edge.source !== edge.target);
  graph.id = `resolution:${run.id}:${run.revision}`;
  graph.name = `${run.name} · 消歧结果`;
  return graph;
}
function matchGraph(run: MatchRun): KnowledgeGraph {
  const graph = copy(matchInputs.get(run.id) ?? baseGraph);
  graph.nodes = graph.nodes.map((node) => ({
    ...node,
    boid: run.nodes.find((item) => item.id === node.id)?.boid ?? null,
  }));
  graph.edges = graph.edges.map((edge) => ({
    ...edge,
    edge_type: run.edges.find((item) => item.id === edge.id)?.edge_type ?? null,
  }));
  graph.id = `match:${run.id}:${run.revision}`;
  graph.name = `${run.name} · 匹配结果`;
  return graph;
}
function inputGraph(id: string): KnowledgeGraph {
  const resolution = resolutionRuns.find((run) =>
    id.startsWith(`resolution:${run.id}:`),
  );
  if (resolution) return resolutionGraph(resolution);
  const match = matchRuns.find((run) => id.startsWith(`match:${run.id}:`));
  if (match) return matchGraph(match);
  return copy({ ...baseGraph, source_id: id });
}
function updateResolution(run: ResolutionRun) {
  const graph = resolutionGraph(run);
  run.summary.node_count = graph.nodes.length;
  run.summary.edge_count = graph.edges.length;
  run.summary.pending_count = run.candidates.filter(
    (item) => item.status === "pending",
  ).length;
  run.summary.merged_count = run.candidates.filter(
    (item) => item.status === "merged",
  ).length;
  run.summary.rejected_count = run.candidates.filter(
    (item) => item.status === "rejected",
  ).length;
  run.merges = run.candidates
    .filter((item) => item.status === "merged")
    .map((item) => ({
      candidate_id: item.id,
      source_nodes: item.nodes,
      target_node:
        item.nodes.find((node) => node.id === item.canonical_id) ??
        item.nodes[0],
    }));
}
function newResolution(kind: SourceKind, source: string): ResolutionRun {
  const graph = inputGraph(source);
  const candidates: ResolutionCandidate[] = candidatePairs
    .filter((ids) =>
      ids.every((id) => graph.nodes.some((node) => node.id === id)),
    )
    .map((ids, index) => {
      const nodes = graph.nodes.filter((node) => ids.includes(node.id));
      const addresses = nodes.map((node) => node.properties.地址);
      const hasAddressConflict =
        addresses.every(Boolean) && new Set(addresses).size > 1;
      return {
        id: `candidate-${index + 1}`,
        node_ids: ids,
        nodes,
        score: [0.98, 0.91, 0.96, 0.94, 0.89][index % 5],
        reasons: [
          "名称与简称相似",
          hasAddressConflict
            ? "共同项目与上下文一致，地址记录待核验"
            : "标识字段一致",
        ],
        evidence: {
          source: "银行与企业关系示例文本",
          method: "预置演示候选",
          origin: "model",
          simulated: true,
          verdict: "uncertain",
          proposal: "same",
          model_reason:
            "预置演示建议，未调用真实模型；请对照两条示例记录核验名称与身份标识。",
          left_quote: String(nodes[0].properties.description ?? nodes[0].name),
          right_quote: String(nodes[1].properties.description ?? nodes[1].name),
          excerpt: `${nodes[0].name}与${nodes[1].name}出现在同一组业务材料中，名称、标识及关联关系提示它们可能指向同一实体。`,
        },
        conflicts: hasAddressConflict
          ? [
              {
                field: "地址",
                values: nodes.map((node) => ({
                  node_id: node.id,
                  value: node.properties.地址,
                })),
              },
            ]
          : [],
        status: "pending",
        canonical_id: null,
      };
    });
  const run: ResolutionRun = {
    id: `demo-resolution-${++serial}`,
    name: kind === "database" ? "客户数据库 · 示例" : title,
    source_kind: kind,
    source_id: source,
    revision: 1,
    status: "ready",
    created_at: now(),
    progress: "示例候选已就绪",
    error: null,
    summary: {
      original_node_count: graph.nodes.length,
      original_edge_count: graph.edges.length,
      node_count: graph.nodes.length,
      edge_count: graph.edges.length,
      pending_count: candidates.length,
      merged_count: 0,
      rejected_count: 0,
    },
    candidates,
    audits: [],
    merges: [],
    diagnostics: {
      method: "前端示例数据",
      warnings: [
        "演示模式：候选与评分均为预置示例，审核结果仅保存在当前页面内存。",
      ],
    },
  };
  resolutionInputs.set(run.id, graph);
  resolutionRuns.unshift(run);
  return run;
}
function newMatch(source: string): MatchRun {
  const graph = inputGraph(source);
  const run: MatchRun = {
    id: `demo-match-${++serial}`,
    name: title,
    source_kind: "graphrag",
    source_id: source,
    revision: 1,
    status: "ready",
    progress: "示例匹配已就绪",
    error: null,
    created_at: now(),
    ontology_revision: "bank-demo-v1",
    confidence_threshold: 0.75,
    summary: {
      node_count: graph.nodes.length,
      edge_count: graph.edges.length,
      matched_nodes: graph.nodes.length - 1,
      matched_edges: graph.edges.length,
    },
    nodes: graph.nodes.map((node, i) => {
      const selected = concepts.find(
        (item) =>
          item.id ===
          (node.type === "PERSON"
            ? "BO_PERSON"
            : node.type === "FINANCIAL_INSTITUTION"
              ? "BO_BANK"
              : node.type === "PROJECT"
                ? "BO_PROJECT"
                : node.type === "FINANCIAL_PRODUCT"
                  ? "BO_LOAN"
                  : "BO_CORP"),
      )!;
      const review = i === 3;
      return {
        id: node.id,
        name: node.name,
        boid: review ? null : selected.id,
        trace: {
          target: "entity",
          name: node.name,
          query: node.name,
          status: review ? "review" : "matched",
          candidates: [selected, concepts[5]],
          selected: { ...selected, score: review ? 0.61 : selected.score },
          confident: !review,
          match_method: "demo",
          detail: "预置演示匹配结果，可在前端手动修改",
        },
      };
    }),
    edges: graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      edge_type: edge.edge_type ?? "RELATED_TO",
      candidates: [edge.edge_type ?? "RELATED_TO", "RELATED_TO", "SERVES"],
      detail: "根据示例关系预置，可人工调整",
      status: "matched",
    })),
    audits: [],
  };
  matchInputs.set(run.id, graph);
  matchRuns.unshift(run);
  return run;
}
newResolution("graphrag", sourceId);
newMatch(sourceId);
function getResolution(id: string) {
  const run = resolutionRuns.find((item) => item.id === id);
  if (!run) throw new Error("示例消歧记录不存在，请刷新页面");
  return run;
}
function getMatch(id: string) {
  const run = matchRuns.find((item) => item.id === id);
  if (!run) throw new Error("示例匹配记录不存在，请刷新页面");
  return run;
}
type Review = { expected_revision: number; reviewer?: string; note?: string };
export const demoKnowledgeApi = {
  config: (_token: string, _signal?: AbortSignal) =>
    reply({
      configured: true,
      model_configured: true,
      runtime_available: true,
      max_upload_bytes: 20971520,
      methods: ["local", "global", "drift"],
    }),
  datasets: (_token: string, _signal?: AbortSignal) => reply(datasets),
  upload: (_token: string, file: File, name: string) => {
    const dataset: Dataset = {
      key: `demo-upload-${++serial}`,
      name: name || file.name,
      status: "ready",
      stage: "示例文件已保存（仅前端）",
      progress: 0,
    };
    datasets.unshift(dataset);
    return reply(dataset);
  },
  index: (_token: string, key: string) => {
    const dataset = datasets.find((item) => item.key === key) ?? datasets[0];
    Object.assign(dataset, {
      status: "succeeded",
      stage: "示例图谱已就绪",
      progress: 1,
    });
    return reply(dataset);
  },
  graph: (_token: string, key: string, _signal?: AbortSignal) =>
    reply({ ...baseGraph, source_id: key }),
  query: (
    _token: string,
    _key: string,
    question: string,
    method: string,
    _signal?: AbortSignal,
  ) =>
    reply({
      answer: `【前端演示回答 · ${method.toUpperCase()}】\n\n当前展示的是固定示例结果，未调用模型或真实检索。\n\n本示例包含 ${baseGraph.nodes.length} 个节点、${baseGraph.edges.length} 条关系，覆盖企业、银行、人物、项目与贷款产品，并串联供应链、授信和担保关系。\n\n华星科技有限公司向远航制造有限公司提供工业软件，双方共同参与智能产线升级项目。城商银行为远航制造提供 800 万元示例授信，恒信担保提供融资担保。林明（示例）为华星科技法定代表人。\n\n图谱中还包含 ${candidatePairs.length} 组名称别名候选，可以进入第四步核验并体验合并、保留及撤销操作。`,
      context: {
        mode: "frontend_demo",
        question,
        method,
        entities: baseGraph.nodes
          .slice(0, 6)
          .map((node) => ({ id: node.id, title: node.name })),
        sources: ["银行与企业关系 · 预置示例文本"],
        note: "以上企业、人物、关系与金额均为演示数据。",
      },
      index_basis: "demo_fixture",
    }),
  sources: (_token: string, _signal?: AbortSignal) =>
    reply<KnowledgeSource[]>([
      { kind: "graphrag", id: sourceId, name: title, root_source_id: sourceId },
      { kind: "database", id: "demo-database", name: "客户数据库 · 示例" },
      ...resolutionRuns.map((run) => ({
        kind: run.source_kind,
        id: `resolution:${run.id}:${run.revision}`,
        name: `${run.name} · 消歧结果`,
        root_source_id: sourceId,
      })),
      ...matchRuns.map((run) => ({
        kind: run.source_kind,
        id: `match:${run.id}:${run.revision}`,
        name: `${run.name} · 匹配结果`,
        root_source_id: sourceId,
      })),
    ]),
  resolutions: (_token: string, _signal?: AbortSignal) => reply(resolutionRuns),
  resolution: (_token: string, id: string, _signal?: AbortSignal) =>
    reply(getResolution(id)),
  resolve: (_token: string, kind: SourceKind, source: string) =>
    reply(newResolution(kind, source)),
  resolutionDecision: (
    _token: string,
    id: string,
    decision: Review & {
      candidate_id: string;
      action: "merge" | "reject" | "reset";
      canonical_id?: string;
    },
  ) => {
    const run = getResolution(id);
    const candidate = run.candidates.find(
      (item) => item.id === decision.candidate_id,
    )!;
    candidate.status =
      decision.action === "merge"
        ? "merged"
        : decision.action === "reject"
          ? "rejected"
          : "pending";
    candidate.canonical_id =
      decision.action === "merge"
        ? (decision.canonical_id ?? candidate.node_ids[0])
        : null;
    run.revision++;
    run.audits.push({
      ...decision,
      id: `audit-${++serial}`,
      created_at: now(),
      revision: run.revision,
    });
    updateResolution(run);
    return reply(run);
  },
  manualMerge: (
    _token: string,
    id: string,
    decision: Review & { node_ids: string[]; canonical_id: string },
  ) => {
    const run = getResolution(id);
    const graph = resolutionGraph(run);
    const existing = run.candidates.find(
      (item) =>
        item.node_ids.length === decision.node_ids.length &&
        item.node_ids.every((node) => decision.node_ids.includes(node)),
    );
    const candidate: ResolutionCandidate = existing ?? {
      id: `manual-${++serial}`,
      node_ids: decision.node_ids,
      nodes: graph.nodes.filter((node) => decision.node_ids.includes(node.id)),
      score: 1,
      reasons: ["人工指定合并（前端演示）"],
      evidence: { mode: "manual_demo" },
      conflicts: [],
      status: "pending",
      canonical_id: null,
    };
    candidate.status = "merged";
    candidate.canonical_id = decision.canonical_id;
    if (!existing) run.candidates.push(candidate);
    run.revision++;
    run.audits.push({
      ...decision,
      id: `audit-${++serial}`,
      action: "merge",
      candidate_id: candidate.id,
      created_at: now(),
      revision: run.revision,
    });
    updateResolution(run);
    return reply(run);
  },
  resolutionGraph: (_token: string, id: string, _signal?: AbortSignal) =>
    reply(resolutionGraph(getResolution(id))),
  resolutionSources: (
    _token: string,
    id: string,
    candidateId: string,
    _signal?: AbortSignal,
  ) => {
    const run = getResolution(id);
    const candidate = run.candidates.find((item) => item.id === candidateId);
    if (!candidate) return Promise.reject(new Error("消歧候选不存在"));
    const graph = resolutionInputs.get(id) ?? baseGraph;
    return reply<ResolutionSources>({
      run_id: id,
      candidate_id: candidateId,
      source_name: `${run.name}（演示数据，无上传文档）`,
      source_kind: run.source_kind,
      nodes: candidate.nodes.map((node, index) => {
        const original = graph.nodes.find((item) => item.id === node.id);
        return {
          node_id: node.id,
          name: node.name,
          quote: String(
            candidate.evidence[index === 0 ? "left_quote" : "right_quote"] ||
              "",
          ),
          quote_location: {
            origin: "unverified",
            message: "预置演示引用，未调用真实模型，也没有上传文档原文",
          },
          records: [
            {
              node_id: node.id,
              name: node.name,
              description: original?.properties.description,
              source_text: null,
              text_unit_ids: [],
              fields:
                run.source_kind === "database"
                  ? (original?.properties ?? {})
                  : null,
            },
          ],
        };
      }),
    });
  },
  matches: (_token: string, _signal?: AbortSignal) => reply(matchRuns),
  match: (_token: string, id: string, _signal?: AbortSignal) =>
    reply(getMatch(id)),
  matchStart: (_token: string, source: string) => reply(newMatch(source)),
  matchAccept: (_token: string, id: string, decision: Review) => {
    const run = getMatch(id);
    if (run.revision !== decision.expected_revision)
      return Promise.reject(new Error("其他操作已更新匹配结果，请刷新后重试"));
    const proposals = run.nodes.filter(
      (node) =>
        !node.boid &&
        !node.reviewed &&
        node.trace.selected &&
        ["review", "matched"].includes(node.trace.status),
    );
    if (!proposals.length) return reply(run);
    run.revision++;
    for (const node of proposals) {
      const before = copy(node);
      node.boid = node.trace.selected!.id;
      node.reviewed = true;
      run.audits.push({
        id: `audit-${++serial}`,
        action: "accept_proposal",
        target: "node",
        target_id: node.id,
        before,
        after: copy(node),
        reviewer: decision.reviewer ?? "人工审核",
        note: decision.note ?? "整体采纳匹配建议",
        created_at: now(),
        revision: run.revision,
      });
    }
    run.summary.matched_nodes = run.nodes.filter((node) => node.boid).length;
    return reply(run);
  },
  matchDecision: (
    _token: string,
    id: string,
    decision: Review & {
      target: "node" | "edge";
      target_id: string;
      boid?: string | null;
      edge_type?: string | null;
    },
  ) => {
    const run = getMatch(id);
    if (decision.target === "node") {
      const node = run.nodes.find((item) => item.id === decision.target_id)!;
      node.boid = decision.boid ?? null;
      node.reviewed = true;
    } else {
      const edge = run.edges.find((item) => item.id === decision.target_id)!;
      edge.edge_type = decision.edge_type ?? null;
      edge.reviewed = true;
      edge.status = edge.edge_type ? "matched" : "unmatched";
    }
    run.revision++;
    run.summary.matched_nodes = run.nodes.filter((node) => node.boid).length;
    run.summary.matched_edges = run.edges.filter(
      (edge) => edge.edge_type,
    ).length;
    run.audits.push({
      ...decision,
      id: `audit-${++serial}`,
      created_at: now(),
      revision: run.revision,
    });
    return reply(run);
  },
  concepts: (_token: string, _id: string, q: string, _signal?: AbortSignal) =>
    reply(
      concepts.filter((item) =>
        `${item.name} ${item.id}`.toLowerCase().includes(q.toLowerCase()),
      ),
    ),
  matchGraph: (_token: string, id: string, _signal?: AbortSignal) =>
    reply(matchGraph(getMatch(id))),
};
