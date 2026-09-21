import { expect, test, type Page } from "@playwright/test";
import type {
  Dataset,
  KnowledgeGraph,
  MatchRun,
  ResolutionRun,
} from "../src/features/knowledge/types";

const originalGraph: KnowledgeGraph = {
  id: "graph1",
  name: "企业关系.txt",
  source_kind: "graphrag",
  source_id: "text1",
  nodes: [
    {
      id: "n1",
      name: "甲企业",
      type: "ORGANIZATION",
      properties: {
        description: "甲企业提供服务",
        account: "0000123",
        amount: "12345678901234567890.1200",
      },
    },
    {
      id: "n2",
      name: "甲公司",
      type: "ORGANIZATION",
      properties: { description: "甲公司为甲企业简称", account: "0000456" },
    },
    {
      id: "n3",
      name: "乙企业",
      type: "ORGANIZATION",
      properties: { description: "乙企业采购服务" },
    },
  ],
  edges: [
    {
      id: "e1",
      source: "n1",
      target: "n3",
      properties: { description: "甲企业为乙企业提供服务", weight: 1 },
    },
  ],
};

async function mockKnowledge(
  page: Page,
  initial = true,
  decorateResolution?: (run: ResolutionRun) => void,
  matchingOptions: {
    decorate?: (run: MatchRun) => void;
    acceptConflictOnce?: boolean;
  } = {},
  resolutionOptions: { resetConflictOnce?: boolean } = {},
) {
  let datasets: Dataset[] = initial
    ? [
        {
          key: "text1",
          name: "企业关系.txt",
          status: "succeeded",
          stage: "索引完成",
          progress: 1,
          error: null,
        },
      ]
    : [];
  let resolution: ResolutionRun | undefined;
  let match: MatchRun | undefined;
  let derivedGraph = structuredClone(originalGraph);
  let matchInputGraph = structuredClone(originalGraph);
  let acceptConflictPending = matchingOptions.acceptConflictOnce;
  let resetConflictPending = resolutionOptions.resetConflictOnce;
  const requests: { path: string; body: Record<string, unknown> }[] = [];
  await page.route(
    /\/api\/v1\/(graphrag|knowledge|resolution)(\/|$)/,
    async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const path = url.pathname.replace("/api/v1", "");
      const body =
        req.method() === "POST" && path !== "/graphrag/uploads"
          ? (req.postDataJSON() as Record<string, unknown>)
          : {};
      if (req.method() === "POST") requests.push({ path, body });
      const send = (value: unknown, status = 200) =>
        route.fulfill({
          status,
          contentType: "application/json",
          body: JSON.stringify(value),
        });
      if (path === "/graphrag/config")
        return send({
          configured: true,
          model_configured: true,
          runtime_available: true,
          max_upload_bytes: 20971520,
          methods: ["local", "global", "drift"],
        });
      if (path === "/graphrag/uploads") {
        expect(req.postDataBuffer()?.toString()).toContain("甲企业");
        datasets = [
          {
            key: "text1",
            name: url.searchParams.get("filename")!,
            status: "ready",
            stage: "已保存文本",
            progress: 0,
          },
        ];
        return send(datasets[0], 201);
      }
      if (path === "/graphrag/datasets") return send(datasets);
      if (path === "/graphrag/datasets/text1/index") {
        datasets[0] = {
          ...datasets[0],
          status: "succeeded",
          stage: "索引完成",
          progress: 1,
        };
        return send(datasets[0], 202);
      }
      if (path === "/graphrag/datasets/text1/graph") return send(originalGraph);
      if (path === "/graphrag/datasets/text1/query/stream") {
        const result = {
          answer: `${body.method}：甲企业向乙企业提供服务。`,
          context: {
            entities: [{ id: "n1", title: "甲企业" }],
            sources: ["企业关系.txt"],
          },
          index_basis: "original_graphrag_index",
          evidence: {
            node_ids: ["n1", "n3"],
            edge_ids: ["e1"],
            cited_node_ids: ["n1"],
            cited_edge_ids: ["e1"],
          },
        };
        return route.fulfill({
          contentType: "application/x-ndjson",
          body:
            JSON.stringify({ event: "token", text: result.answer }) +
            "\n" +
            JSON.stringify({ event: "result", result }) +
            "\n",
        });
      }
      if (path === "/knowledge/sources")
        return send([
          {
            kind: "graphrag",
            id: "text1",
            name: "企业关系.txt",
            root_source_id: "text1",
          },
          ...(resolution?.source_kind === "graphrag"
            ? [
                {
                  kind: "graphrag",
                  id: `resolution:${resolution.id}:${resolution.revision}`,
                  name: "企业关系.txt · 消歧结果",
                  root_source_id: "text1",
                },
              ]
            : []),
          ...(match
            ? [
                {
                  kind: "graphrag",
                  id: `match:${match.id}:${match.revision}`,
                  name: "企业关系.txt · 匹配结果",
                  root_source_id: "text1",
                },
              ]
            : []),
          { kind: "database", id: "db1", name: "客户数据库" },
        ]);
      if (path === "/knowledge/matches" && req.method() === "GET")
        return send(match ? [match] : []);
      if (path === "/knowledge/matches" && req.method() === "POST") {
        matchInputGraph = structuredClone(
          String(body.source_id).startsWith("resolution:")
            ? derivedGraph
            : originalGraph,
        );
        match = {
          id: "match1",
          name: "企业关系.txt",
          source_kind: "graphrag",
          source_id: body.source_id as string,
          status: "ready",
          progress: "匹配完成",
          error: null,
          revision: 1,
          created_at: "2026-09-20T12:00:00Z",
          ontology_revision: "bank-v1",
          confidence_threshold: 0.75,
          summary: {
            node_count: matchInputGraph.nodes.length,
            edge_count: 1,
            matched_nodes: 1,
            matched_edges: 1,
          },
          nodes: matchInputGraph.nodes.map((node) => ({
            id: node.id,
            name: node.name,
            boid: node.id === "n1" ? "BO_CORP" : null,
            trace: {
              target: "entity",
              name: node.name,
              query: `${node.name} 对公客户`,
              status: node.id === "n1" ? "matched" : "review",
              candidates:
                node.id === "n1"
                  ? [
                      {
                        id: "BO_CORP",
                        name: "对公客户",
                        score: 0.98,
                        parents: [],
                      },
                      { id: "BO_ORG", name: "组织", score: 0.61, parents: [] },
                    ]
                  : [
                      { id: "BO_ORG", name: "组织", score: 0.61, parents: [] },
                      {
                        id: "BO_CORP",
                        name: "对公客户",
                        score: 0.41,
                        parents: [],
                      },
                    ],
              selected:
                node.id === "n1"
                  ? {
                      id: "BO_CORP",
                      name: "对公客户",
                      score: 0.98,
                      parents: [],
                    }
                  : { id: "BO_ORG", name: "组织", score: 0.61, parents: [] },
              confident: node.id === "n1",
              match_method: "retrieve",
              detail: `复用 retrieve 候选，得分 ${node.id === "n1" ? "0.980" : "0.610"}`,
            },
          })),
          edges: [
            {
              id: "e1",
              source: "n1",
              target: "n3",
              edge_type: "SERVES",
              candidates: ["SERVES", "RELATED_TO"],
              detail: "根据两端本体关系约束匹配",
              status: "matched",
            },
          ],
          audits: [],
        };
        matchingOptions.decorate?.(match);
        return send(match, 202);
      }
      if (path === "/knowledge/matches/match1") return send(match);
      if (path === "/knowledge/matches/match1/concepts")
        return send([
          { id: "BO_CORP", name: "对公客户", parents: [] },
          { id: "BO_ORG", name: "组织", parents: [] },
        ]);
      if (path === "/knowledge/matches/match1/graph")
        return send({
          ...matchInputGraph,
          nodes: matchInputGraph.nodes.map((node) => ({
            ...node,
            boid: match?.nodes.find((item) => item.id === node.id)?.boid,
          })),
          edges: matchInputGraph.edges.map((edge) => ({
            ...edge,
            edge_type: match?.edges.find((item) => item.id === edge.id)
              ?.edge_type,
          })),
        });
      if (path === "/knowledge/matches/match1/decisions" && match) {
        expect(body.expected_revision).toBe(match.revision);
        match.revision++;
        if (body.target === "node") {
          const node = match.nodes.find((item) => item.id === body.target_id)!;
          node.boid = body.boid as string | null;
          node.reviewed = true;
        } else {
          const edge = match.edges.find((item) => item.id === body.target_id)!;
          edge.edge_type = body.edge_type as string | null;
          edge.reviewed = true;
        }
        match.summary.matched_nodes = match.nodes.filter(
          (node) => node.boid,
        ).length;
        match.summary.matched_edges = match.edges.filter(
          (edge) => edge.edge_type,
        ).length;
        match.audits.push({
          id: `a${match.revision}`,
          target: body.target as string,
          target_id: body.target_id as string,
          reviewer: body.reviewer as string,
          note: body.note as string,
          created_at: "2026-09-20T12:10:00Z",
          revision: match.revision,
        });
        return send(match);
      }
      if (path === "/knowledge/matches/match1/accept-proposals" && match) {
        expect(body.expected_revision).toBe(match.revision);
        if (acceptConflictPending) {
          acceptConflictPending = false;
          match.revision++;
          const node = match.nodes.find((item) => item.id === "n2")!;
          node.boid = null;
          node.reviewed = true;
          return send({ detail: "匹配版本已更新，请刷新后重试" }, 409);
        }
        match.revision++;
        for (const node of match.nodes) {
          if (node.boid || node.reviewed || !node.trace.selected) continue;
          node.boid = node.trace.selected.id;
          node.reviewed = true;
          match.audits.push({
            id: `accepted-${node.id}-${match.revision}`,
            target: "node",
            target_id: node.id,
            action: "accept_proposal",
            created_at: "2026-09-20T12:15:00Z",
            revision: match.revision,
          });
        }
        for (const edge of match.edges) {
          if (edge.edge_type || edge.reviewed || !edge.proposed_edge_type)
            continue;
          edge.edge_type = edge.proposed_edge_type;
          edge.reviewed = true;
          match.audits.push({
            id: `accepted-${edge.id}-${match.revision}`,
            target: "edge",
            target_id: edge.id,
            action: "accept_proposal",
            created_at: "2026-09-20T12:15:00Z",
            revision: match.revision,
          });
        }
        match.summary.matched_nodes = match.nodes.filter(
          (node) => node.boid,
        ).length;
        match.summary.matched_edges = match.edges.filter(
          (edge) => edge.edge_type,
        ).length;
        return send(match);
      }
      if (path === "/resolution/runs" && req.method() === "GET")
        return send(resolution ? [resolution] : []);
      if (path === "/resolution/runs" && req.method() === "POST") {
        derivedGraph = structuredClone(originalGraph);
        resolution = {
          id: `resolution${requests.filter((item) => item.path === "/resolution/runs").length}`,
          name: body.source_kind === "database" ? "客户数据库" : "企业关系.txt",
          source_kind: body.source_kind as "graphrag" | "database",
          source_id: body.source_id as string,
          revision: 1,
          status: "ready",
          created_at: "2026-09-20T12:00:00Z",
          progress: "候选分析完成",
          error: null,
          summary: {
            original_node_count: 3,
            original_edge_count: 1,
            node_count: 3,
            edge_count: 1,
            pending_count: 1,
            merged_count: 0,
            rejected_count: 0,
          },
          candidates: [
            {
              id: "candidate1",
              node_ids: ["n1", "n2"],
              nodes: originalGraph.nodes.slice(0, 2),
              score: 0.95,
              reasons: ["名称别名匹配", "共享来源上下文"],
              evidence: {
                documents: ["企业关系.txt"],
                origin: "model",
                verdict: "uncertain",
                proposal: "same",
                model_reason: "原文明确说明甲公司为甲企业简称",
                left_quote: "甲企业提供服务",
                right_quote: "甲公司为甲企业简称",
              },
              conflicts: [
                {
                  field: "account",
                  values: [
                    { node_id: "n1", value: "0000123" },
                    { node_id: "n2", value: "0000456" },
                  ],
                },
              ],
              status: "pending",
              canonical_id: null,
            },
          ],
          audits: [],
          merges: [],
          diagnostics: { method: "name-and-identifiers" },
        };
        decorateResolution?.(resolution);
        return send(resolution, 202);
      }
      if (/^\/resolution\/runs\/resolution\d+$/.test(path))
        return send(resolution);
      if (/^\/resolution\/runs\/resolution\d+\/graph$/.test(path))
        return send(derivedGraph);
      if (
        /^\/resolution\/runs\/[^/]+\/candidates\/[^/]+\/sources$/.test(path)
      ) {
        const candidateId = path.split("/")[5];
        const candidate = resolution?.candidates.find(
          (item) => item.id === candidateId,
        );
        const sourceKind = resolution?.source_kind ?? "graphrag";
        return send({
          run_id: path.split("/")[3],
          candidate_id: candidateId,
          source_name: resolution?.name ?? "企业关系.txt",
          source_kind: sourceKind,
          nodes: (candidate?.nodes ?? originalGraph.nodes.slice(0, 2)).map(
            (node, index) => {
              const quote =
                index === 0 ? "甲企业提供服务" : "甲公司为甲企业简称";
              return {
                node_id: node.id,
                name: node.name,
                quote: sourceKind === "graphrag" ? quote : null,
                quote_location: {
                  origin:
                    sourceKind === "graphrag" ? "source_text" : "source_record",
                  message:
                    sourceKind === "graphrag"
                      ? "引用可在关联原文中逐字找到"
                      : "来源为数据库字段记录",
                },
                records: [
                  {
                    node_id: node.id,
                    name: node.name,
                    description: null,
                    source_text:
                      sourceKind === "graphrag"
                        ? `来源文档第 ${index + 1} 段：${quote}。保留段落后的完整上下文。`
                        : null,
                    text_unit_ids:
                      sourceKind === "graphrag" ? [`source-${node.id}`] : [],
                    fields:
                      sourceKind === "database" ? { name: node.name } : null,
                  },
                ],
              };
            },
          ),
        });
      }
      if (
        (path.endsWith("/decisions") || path.endsWith("/manual")) &&
        resolution
      ) {
        expect(body.expected_revision).toBe(resolution.revision);
        if (body.action === "reset" && resetConflictPending) {
          resetConflictPending = false;
          resolution.revision++;
          return send({ detail: "消歧版本已更新，请刷新后重试" }, 409);
        }
        resolution.revision++;
        const candidate = resolution.candidates[0];
        const previousStatus = candidate.status;
        const action = path.endsWith("/manual") ? "manual" : body.action;
        candidate.status =
          action === "merge" || action === "manual"
            ? "merged"
            : action === "reject"
              ? "rejected"
              : "pending";
        candidate.canonical_id = (body.canonical_id as string) || null;
        resolution.summary.pending_count =
          candidate.status === "pending" ? 1 : 0;
        resolution.summary.merged_count = candidate.status === "merged" ? 1 : 0;
        resolution.summary.rejected_count =
          candidate.status === "rejected" ? 1 : 0;
        resolution.summary.node_count = candidate.status === "merged" ? 2 : 3;
        resolution.merges =
          candidate.status === "merged"
            ? [
                {
                  candidate_id: candidate.id,
                  source_nodes: candidate.nodes,
                  target_node: candidate.nodes.find(
                    (node) => node.id === candidate.canonical_id,
                  )!,
                },
              ]
            : [];
        derivedGraph = structuredClone(originalGraph);
        if (candidate.status === "merged")
          derivedGraph.nodes = derivedGraph.nodes.filter(
            (node) => node.id !== "n2",
          );
        resolution.audits.push({
          id: `audit${resolution.revision}`,
          action: String(action),
          candidate_id: candidate.id,
          previous_status: previousStatus,
          source_nodes: structuredClone(candidate.nodes),
          canonical_id: candidate.canonical_id ?? undefined,
          reviewer: body.reviewer as string,
          note: body.note as string,
          created_at: "2026-09-20T12:20:00Z",
          revision: resolution.revision,
        });
        return send(resolution);
      }
      return send({ detail: `Missing mock: ${path}` }, 404);
    },
  );
  return requests;
}

test("TXT ingestion, graph details and all four streaming query methods", async ({
  page,
}) => {
  const requests = await mockKnowledge(page, false);
  await page.goto("/");
  await page.getByRole("tab", { name: "TXT 文本", exact: true }).click();
  await page.getByLabel("选择 TXT 文本文件").setInputFiles({
    name: "企业关系.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("甲企业向乙企业提供服务。甲公司是甲企业简称。"),
  });
  await page.getByRole("button", { name: "保存 TXT 文本（1 个文件）" }).click();
  await expect(
    page.getByText("已保存 1 个文本", { exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "03 GraphRAG 图谱与问答" }).click();
  await page.getByRole("button", { name: "开始 GraphRAG 索引" }).click();
  await expect(
    page.getByRole("tab", { name: "生成图谱", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "原始属性", exact: true })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toContainText(
    "12345678901234567890.1200",
  );
  await expect(page.getByRole("dialog")).toContainText("0000123");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("tab", { name: "图谱问答", exact: true }).click();
  await expect(page.getByText(/问答依据原始 GraphRAG 索引/)).toBeVisible();
  await page.getByLabel("你的问题").fill("甲企业与乙企业有什么关系？");
  for (const [method, label] of [
    ["local", "Local · 实体与关系"],
    ["global", "Global · 全局主题"],
    ["drift", "DRIFT · 扩展检索"],
    ["basic", "Basic · 原文向量检索"],
  ]) {
    await page.getByRole("combobox", { name: "检索方式" }).click();
    await page.getByText(label, { exact: true }).last().click();
    await page.getByRole("button", { name: "查询图谱", exact: true }).click();
    await expect(page.locator(".knowledge-answer:visible")).toContainText(
      `${method}：`,
    );
  }
  await page.getByText("检索证据与上下文", { exact: true }).click();
  await expect(
    page.locator(".knowledge-json").filter({ hasText: "sources" }),
  ).toContainText("企业关系.txt");
  expect(
    requests
      .filter((item) => item.path.endsWith("/query/stream"))
      .map((item) => item.body.method),
  ).toEqual(["local", "global", "drift", "basic"]);
});

test("GraphRAG matching exposes retrieval scores, manual annotations and original fields", async ({
  page,
}) => {
  const requests = await mockKnowledge(page);
  await page.goto("/");
  await page.getByRole("tab", { name: "03 GraphRAG 图谱与问答" }).click();
  await page.getByRole("tab", { name: "节点与边匹配", exact: true }).click();
  await page.getByRole("button", { name: "开始节点与边匹配" }).click();
  await expect(
    page.getByRole("heading", { name: "匹配结果", exact: true }),
  ).toBeVisible();
  const suggestedNode = page.locator(
    '.knowledge-match-results tr[data-row-key="n2"]',
  );
  await expect(suggestedNode).toContainText("组织");
  await expect(suggestedNode).toContainText("BO_ORG");
  await expect(suggestedNode).toContainText("0.610");
  await expect(suggestedNode).toContainText("匹配建议");
  await expect(suggestedNode).toContainText("未挂载");
  await page.getByText("本体版本与匹配说明", { exact: true }).click();
  await expect(page.getByText(/自动采用阈值：0.750/)).toBeVisible();
  await page
    .getByRole("button", { name: "展开行", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("cell", { name: "0.980", exact: true }).first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "修改节点", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("combobox", { name: "选择本体概念" }).click();
  await page.getByText("组织 · BO_ORG · 0.610", { exact: true }).click();
  await dialog.getByLabel("审核人", { exact: true }).fill("张审核");
  await dialog.getByLabel("修改原因", { exact: true }).fill("根据实际实体调整");
  await dialog.getByRole("button", { name: "保存修改" }).click();
  await expect(dialog).not.toBeVisible();
  const revisedNode = page.locator(
    '.knowledge-match-results tr[data-row-key="n1"]',
  );
  await expect(revisedNode).toContainText("BO_ORG");
  await expect(revisedNode).toContainText("人工已确认");
  await page.getByRole("tab", { name: "边类型匹配", exact: true }).click();
  await page.getByRole("button", { name: "修改边类型", exact: true }).click();
  await dialog.getByRole("combobox", { name: "选择边类型" }).click();
  await page.getByText("RELATED_TO", { exact: true }).last().click();
  await dialog.getByRole("button", { name: "保存修改" }).click();
  await page.getByRole("tab", { name: "人工修改记录（2）" }).click();
  await expect(
    page.getByRole("cell", { name: "根据实际实体调整" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "匹配后图谱", exact: true }).click();
  await page
    .getByRole("button", { name: "原始属性", exact: true })
    .first()
    .click();
  await expect(dialog).toContainText("ORGANIZATION");
  await expect(dialog).toContainText("12345678901234567890.1200");
  await expect(dialog).toContainText("BO_ORG");
  expect(
    requests
      .filter((item) => item.path.endsWith("/decisions"))
      .map((item) => item.body),
  ).toMatchObject([
    { target: "node", target_id: "n1", boid: "BO_ORG", expected_revision: 1 },
    {
      target: "edge",
      target_id: "e1",
      edge_type: "RELATED_TO",
      expected_revision: 2,
    },
  ]);
});

test("GraphRAG proposals can be accepted together while retaining evidence and manual clear decisions", async ({
  page,
}) => {
  const requests = await mockKnowledge(page, true, undefined, {
    decorate: (run) => {
      run.nodes.find((node) => node.id === "n2")!.boid = "BO_ORG";
      run.summary.matched_nodes = 2;
      run.edges[0].edge_type = null;
      run.edges[0].proposed_edge_type = "SERVES";
      run.edges[0].status = "review";
      run.summary.matched_edges = 0;
    },
  });
  await page.goto("/");
  await page.getByRole("tab", { name: "03 GraphRAG 图谱与问答" }).click();
  await page.getByRole("tab", { name: "节点与边匹配", exact: true }).click();
  await page.getByRole("button", { name: "开始节点与边匹配" }).click();
  await page.screenshot({
    path: "test-results/matching-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  const originalNode = page.locator(
    '.knowledge-match-results tr[data-row-key="n2"]',
  );
  await originalNode
    .getByRole("button", { name: "修改节点", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.locator(".ant-select").hover();
  await dialog.locator(".ant-select-clear").click();
  await dialog
    .getByLabel("修改原因", { exact: true })
    .fill("保留原节点，不挂载本体");
  await dialog.getByRole("button", { name: "保存修改" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(originalNode).toContainText("人工已清除");
  await expect(page.getByText("1 / 3", { exact: true })).toBeVisible();
  const accept = page.getByRole("button", {
    name: "整体采纳匹配建议",
    exact: true,
  });
  await accept.click();
  await expect(page.getByText("2 / 3", { exact: true })).toBeVisible();
  await expect(page.getByText("1 / 1", { exact: true })).toBeVisible();
  await expect(originalNode).toContainText("未挂载");
  await expect(originalNode).toContainText("人工已清除");
  const acceptedNode = page.locator(
    '.knowledge-match-results tr[data-row-key="n3"]',
  );
  await expect(acceptedNode).toContainText("BO_ORG");
  await expect(acceptedNode).toContainText("0.610");
  await expect(acceptedNode).toContainText("人工已确认");
  await acceptedNode
    .getByRole("button", { name: "展开行", exact: true })
    .click();
  await expect(page.locator(".knowledge-match-evidence")).toContainText(
    "乙企业 对公客户",
  );
  await expect(page.locator(".knowledge-match-evidence")).toContainText(
    "0.610",
  );
  await expect(accept).toBeDisabled();
  await page.getByRole("tab", { name: "边类型匹配", exact: true }).click();
  const acceptedEdge = page.locator(
    '.knowledge-match-results tr[data-row-key="e1"]',
  );
  await expect(acceptedEdge).toContainText("甲企业 → 乙企业");
  await expect(acceptedEdge).toContainText("SERVES");
  await expect(acceptedEdge).toContainText("人工已确认");
  expect(
    requests.find((item) => item.path.endsWith("/decisions"))?.body,
  ).toMatchObject({
    target: "node",
    target_id: "n2",
    boid: null,
    expected_revision: 1,
  });
  expect(
    requests
      .filter((item) => item.path.endsWith("/accept-proposals"))
      .map((item) => item.body),
  ).toEqual([{ expected_revision: 2 }]);
  await page.getByRole("tab", { name: "节点匹配过程", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "匹配结果", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/matching-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
});

test("GraphRAG bulk acceptance refreshes a conflicting revision and respects a concurrent manual clear", async ({
  page,
}) => {
  const requests = await mockKnowledge(page, true, undefined, {
    acceptConflictOnce: true,
  });
  await page.goto("/");
  await page.getByRole("tab", { name: "03 GraphRAG 图谱与问答" }).click();
  await page.getByRole("tab", { name: "节点与边匹配", exact: true }).click();
  await page.getByRole("button", { name: "开始节点与边匹配" }).click();
  const accept = page.getByRole("button", {
    name: "整体采纳匹配建议",
    exact: true,
  });
  await accept.click();
  await expect(
    page.getByText("匹配版本已更新，请刷新后重试", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("修订 2", { exact: true })).toBeVisible();
  const clearedNode = page.locator(
    '.knowledge-match-results tr[data-row-key="n2"]',
  );
  await expect(clearedNode).toContainText("人工已清除");
  await expect(page.getByText("1 / 3", { exact: true })).toBeVisible();
  await accept.click();
  await expect(page.getByText("2 / 3", { exact: true })).toBeVisible();
  await expect(clearedNode).toContainText("未挂载");
  await expect(clearedNode).toContainText("人工已清除");
  await expect(
    page.locator('.knowledge-match-results tr[data-row-key="n3"]'),
  ).toContainText("人工已确认");
  await expect(accept).toBeDisabled();
  expect(
    requests
      .filter((item) => item.path.endsWith("/accept-proposals"))
      .map((item) => item.body),
  ).toEqual([{ expected_revision: 1 }, { expected_revision: 2 }]);
});

test("both graph sources combine merge review and support cancellation, rollback, remerge and manual rollback", async ({
  page,
}) => {
  test.setTimeout(60_000);
  const requests = await mockKnowledge(page);
  const resets = () => requests.filter((item) => item.body.action === "reset");
  await page.goto("/");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  for (const source of ["GraphRAG · 企业关系.txt", "数据库 · 客户数据库"]) {
    await page.getByRole("combobox", { name: "待消歧图谱" }).click();
    await page.getByText(source, { exact: true }).last().click();
    await page.getByRole("button", { name: "分析消歧候选" }).click();
    await page.getByRole("tab", { name: "合并建议（1）", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "内容差异（1 项）", exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel("待核验实体对，尚未合并")).toBeVisible();
    await expect(page.getByLabel("2 个节点合并为 1 个节点")).toHaveCount(0);
    await page.getByLabel("消歧审核人").fill("李审核");
    await page.getByLabel("消歧审核备注").fill("已核实为同一企业");
    await page.getByRole("button", { name: "确认合并", exact: true }).click();
    await expect(page.getByText("3 → 2", { exact: true })).toBeVisible();
    await page
      .getByRole("tab", { name: "合并与审核（1）", exact: true })
      .click();
    await expect(
      page.getByRole("tab", { name: /^合并过程|^审核历史/ }),
    ).toHaveCount(0);
    await expect(page.getByLabel("2 个节点合并为 1 个节点")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "已合并 2 个实体" }),
    ).toBeVisible();
    const audits = page.getByRole("region", { name: "审核记录", exact: true });
    await expect(
      audits.getByRole("cell", { name: "李审核", exact: true }),
    ).toBeVisible();
    await expect(
      audits.getByRole("cell", { name: "甲企业 / 甲公司", exact: true }),
    ).toBeVisible();
    await expect(
      audits.getByRole("cell", { name: "已核实为同一企业", exact: true }),
    ).toBeVisible();
    if (source.startsWith("GraphRAG")) {
      await page.getByRole("button", { name: "切换深浅主题" }).click();
      await page.screenshot({
        path: "../outputs/resolution-review-fixture.png",
        fullPage: true,
        animations: "disabled",
      });
    }
    const resetsBeforeCancel = resets().length;
    await page.getByRole("button", { name: "退回合并", exact: true }).click();
    const rollback = page.getByRole("dialog", {
      name: "退回合并",
      exact: true,
    });
    await expect(rollback).toContainText("甲企业");
    await expect(rollback).toContainText("甲公司");
    await rollback.getByRole("button", { name: "取消", exact: true }).click();
    await expect(rollback).not.toBeVisible();
    expect(resets()).toHaveLength(resetsBeforeCancel);
    await expect(page.getByText("3 → 2", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "退回合并", exact: true }).click();
    await rollback.getByLabel("退回审核人", { exact: true }).fill("王复核");
    await rollback
      .getByLabel("退回原因", { exact: true })
      .fill("账号不同，需要重新核验");
    await rollback
      .getByRole("button", { name: "确认退回", exact: true })
      .click();
    await expect(rollback).not.toBeVisible();
    await expect(page.getByText("3 → 3", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("tab", { name: "合并与审核（2）", exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel("2 个节点合并为 1 个节点")).toHaveCount(0);
    await expect(
      page.getByText("暂无生效中的合并", { exact: true }),
    ).toBeVisible();
    await expect(
      audits.getByRole("cell", { name: "退回合并", exact: true }),
    ).toBeVisible();
    await expect(
      audits.getByRole("cell", { name: "王复核", exact: true }),
    ).toBeVisible();
    await expect(
      audits.getByRole("cell", { name: "账号不同，需要重新核验", exact: true }),
    ).toBeVisible();
    await expect(
      audits.getByRole("cell", { name: "已核实为同一企业", exact: true }),
    ).toBeVisible();
    expect(resets().at(-1)?.body).toEqual({
      candidate_id: "candidate1",
      action: "reset",
      expected_revision: 2,
      reviewer: "王复核",
      note: "账号不同，需要重新核验",
    });

    await page.getByRole("tab", { name: "合并建议（1）", exact: true }).click();
    await expect(page.getByLabel("待核验实体对，尚未合并")).toBeVisible();
    await page.getByRole("button", { name: "确认合并", exact: true }).click();
    await expect(page.getByText("3 → 2", { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "分析记录（1）", exact: true }).click();
    await page.getByRole("combobox", { name: "候选状态" }).click();
    await page.getByText("已合并", { exact: true }).last().click();
    await expect(
      page.getByRole("button", { name: "撤销审核决定", exact: true }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "退回合并", exact: true }).click();
    await expect(rollback).toBeVisible();
    await rollback
      .getByLabel("退回原因", { exact: true })
      .fill("从分析记录退回重新核验");
    await rollback
      .getByRole("button", { name: "确认退回", exact: true })
      .click();
    await expect(rollback).not.toBeVisible();
    await expect(page.getByText("3 → 3", { exact: true })).toBeVisible();
    expect(resets().at(-1)?.body).toMatchObject({
      expected_revision: 4,
      note: "从分析记录退回重新核验",
    });
  }
  await page.getByRole("button", { name: "人工指定合并" }).click();
  const dialog = page.getByRole("dialog", {
    name: "人工指定实体合并",
    exact: true,
  });
  const choices = dialog.getByRole("combobox", {
    name: "人工合并节点",
    exact: true,
  });
  await choices.click();
  await page.getByText("甲企业 · n1", { exact: true }).last().click();
  await page.getByText("甲公司 · n2", { exact: true }).last().click();
  await dialog.getByText("人工指定实体合并", { exact: true }).click();
  await dialog.getByRole("button", { name: "确认合并", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("tab", { name: "合并与审核（5）", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "人工指定合并", exact: true }),
  ).toBeVisible();
  const activeMerges = page.getByRole("region", {
    name: "当前生效的合并",
    exact: true,
  });
  await expect(
    activeMerges.getByLabel("2 个节点合并为 1 个节点"),
  ).toBeVisible();
  expect(
    requests
      .filter((item) => item.path === "/resolution/runs")
      .map((item) => item.body.source_kind),
  ).toEqual(["graphrag", "database"]);
  expect(
    requests.find((item) => item.path.endsWith("/manual"))?.body,
  ).toMatchObject({
    node_ids: ["n1", "n2"],
    canonical_id: "n1",
    expected_revision: 5,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    activeMerges.getByLabel("2 个节点合并为 1 个节点"),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/resolution-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "退回合并", exact: true }).click();
  const rollback = page.getByRole("dialog", { name: "退回合并", exact: true });
  await expect(rollback).toBeVisible();
  const bounds = await rollback.boundingBox();
  expect(bounds?.x).toBeGreaterThanOrEqual(0);
  expect((bounds?.x ?? 0) + (bounds?.width ?? 0)).toBeLessThanOrEqual(390);
  await rollback
    .getByLabel("退回原因", { exact: true })
    .fill("退回人工指定的合并");
  await rollback.getByRole("button", { name: "确认退回", exact: true }).click();
  await expect(rollback).not.toBeVisible();
  await expect(page.getByText("3 → 3", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "合并与审核（6）", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "退回合并", exact: true }),
  ).toHaveCount(3);
  await expect(
    page.getByRole("cell", { name: "退回人工指定的合并", exact: true }),
  ).toBeVisible();
  expect(resets().at(-1)?.body).toMatchObject({ expected_revision: 6 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
});

test("resolution rollback keeps a conflicting merge and refreshes before retrying with a new revision", async ({
  page,
}) => {
  const requests = await mockKnowledge(
    page,
    true,
    undefined,
    {},
    { resetConflictOnce: true },
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  await page.getByRole("button", { name: "分析消歧候选", exact: true }).click();
  await page.getByRole("button", { name: "确认合并", exact: true }).click();
  await page.getByRole("tab", { name: "合并与审核（1）", exact: true }).click();
  await page.getByRole("button", { name: "退回合并", exact: true }).click();
  const rollback = page.getByRole("dialog", { name: "退回合并", exact: true });
  await rollback.getByLabel("退回审核人", { exact: true }).fill("陈复核");
  await rollback
    .getByLabel("退回原因", { exact: true })
    .fill("发现不同的账号证据");
  await rollback.getByRole("button", { name: "确认退回", exact: true }).click();
  await expect(rollback).toBeVisible();
  await expect(rollback).toContainText("消歧版本已更新，请刷新后重试");
  await expect(rollback).toContainText(
    "审核记录已更新，请关闭窗口并重新选择要退回的合并。",
  );
  await expect(
    rollback.getByRole("button", { name: "确认退回", exact: true }),
  ).toBeDisabled();
  await expect(rollback.getByLabel("退回原因", { exact: true })).toHaveValue(
    "发现不同的账号证据",
  );
  await expect(page.getByText("3 → 2", { exact: true })).toBeVisible();
  await expect(page.getByLabel("2 个节点合并为 1 个节点")).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "合并与审核（1）", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "退回合并", exact: true }),
  ).toHaveCount(0);
  await rollback.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "退回合并", exact: true }).click();
  await expect(
    rollback.getByRole("button", { name: "确认退回", exact: true }),
  ).toBeEnabled();
  await rollback.getByRole("button", { name: "确认退回", exact: true }).click();
  await expect(rollback).not.toBeVisible();
  await expect(page.getByText("3 → 3", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "退回合并", exact: true }),
  ).toBeVisible();
  expect(
    requests
      .filter((item) => item.body.action === "reset")
      .map((item) => item.body),
  ).toEqual([
    {
      candidate_id: "candidate1",
      action: "reset",
      expected_revision: 2,
      reviewer: "陈复核",
      note: "发现不同的账号证据",
    },
    {
      candidate_id: "candidate1",
      action: "reset",
      expected_revision: 3,
      reviewer: "陈复核",
      note: "发现不同的账号证据",
    },
  ]);

  await page.reload();
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  await page.getByRole("tab", { name: "合并与审核（2）", exact: true }).click();
  await expect(page.getByText("3 → 3", { exact: true })).toBeVisible();
  await expect(
    page.getByText("暂无生效中的合并", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "合并", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "退回合并", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "发现不同的账号证据", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "合并建议（1）", exact: true }),
  ).toBeVisible();
});

test("rejected resolution decisions retain their direct undo action", async ({
  page,
}) => {
  const requests = await mockKnowledge(page);
  await page.goto("/");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  await page.getByRole("button", { name: "分析消歧候选", exact: true }).click();
  await page
    .getByRole("button", { name: "保留为不同实体", exact: true })
    .click();
  await page.getByRole("tab", { name: "分析记录（1）", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退回合并", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "撤销审核决定", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "退回合并", exact: true }),
  ).not.toBeVisible();
  await page.getByRole("tab", { name: "合并与审核（2）", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "保留独立节点", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "撤销决定", exact: true }),
  ).toBeVisible();
  expect(
    requests.filter((item) => item.body.action === "reset").at(-1)?.body,
  ).toMatchObject({
    candidate_id: "candidate1",
    action: "reset",
    expected_revision: 2,
  });
});

test("resolution differences compare readable content and preserve collapsed GraphRAG metadata", async ({
  page,
}) => {
  const leftId = "345bda0a-337e-412f-8295-4f2c8a2e99c4";
  const rightId = "dc5e117d-8b2d-4270-82d2-73693e32c8b5";
  const leftHash = "1286a4f42f51fcbeafa38ae7084179bdff".repeat(4);
  const rightHash = "eecff5ca25c299f087af0e4260a45bb0e7".repeat(4);
  const leftDescription =
    "美国联邦政府官方出版物，于2019年10月9日刊发BIS规则（84 FR 54004）将科大讯飞列入实体清单；2020年7月22日刊发新规（85 FR 44159）新增涉疆企业";
  const rightDescription =
    "美国联邦政府官方出版物，2025年11月12日发布BIS关于暂停关联方规则的最终规则（编号2025-19846）";
  await mockKnowledge(page, true, (run) => {
    const candidate = run.candidates[0];
    candidate.node_ids = [leftId, rightId];
    candidate.nodes = [
      { id: leftId, name: "美国联邦公报", type: "ORGANIZATION" },
      { id: rightId, name: "联邦公报", type: "ORGANIZATION" },
    ];
    candidate.conflicts = [
      {
        field: "description",
        values: [
          { node_id: rightId, value: rightDescription },
          { node_id: leftId, value: leftDescription },
        ],
      },
      {
        field: "human_readable_id",
        values: [
          { node_id: leftId, value: 81 },
          { node_id: rightId, value: 125 },
        ],
      },
      {
        field: "id",
        values: [
          { node_id: leftId, value: leftId },
          { node_id: rightId, value: rightId },
        ],
      },
      {
        field: "text_unit_ids",
        values: [
          { node_id: leftId, value: [leftHash] },
          { node_id: rightId, value: [rightHash] },
        ],
      },
      {
        field: "title",
        values: [
          { node_id: rightId, value: "联邦公报" },
          { node_id: leftId, value: "美国联邦公报" },
        ],
      },
    ];
  });
  await page.goto("/");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  await page.getByRole("button", { name: "分析消歧候选", exact: true }).click();
  const differences = page.getByRole("region", { name: "差异对照" });
  await expect(
    differences.getByRole("heading", { name: "内容差异（2 项）", exact: true }),
  ).toBeVisible();
  const content = differences.getByRole("table", {
    name: "内容差异",
    exact: true,
  });
  await expect(content.getByRole("columnheader")).toHaveText([
    "对比项",
    "左侧 · 美国联邦公报",
    "右侧 · 联邦公报",
  ]);
  await expect(content.getByRole("rowheader")).toHaveText([
    "名称",
    "描述（抽取结果）",
  ]);
  const names = content.getByRole("row").nth(1).getByRole("cell");
  await expect(names.nth(0).locator(".resolution-comparison-value")).toHaveText(
    "美国联邦公报",
  );
  await expect(names.nth(1).locator(".resolution-comparison-value")).toHaveText(
    "联邦公报",
  );
  const descriptions = content.getByRole("row").nth(2).getByRole("cell");
  await expect(
    descriptions.nth(0).locator(".resolution-comparison-value"),
  ).toHaveText(leftDescription);
  await expect(
    descriptions.nth(1).locator(".resolution-comparison-value"),
  ).toHaveText(rightDescription);
  for (const value of [leftId, rightId, leftHash, rightHash])
    await expect(differences.getByText(value, { exact: true })).toBeHidden();
  await differences.screenshot({
    path: "test-results/resolution-readable-differences.png",
    animations: "disabled",
  });
  await differences
    .getByRole("button", { name: "系统记录差异（3 项）", exact: true })
    .click();
  const metadata = differences.getByRole("table", {
    name: "系统记录差异",
    exact: true,
  });
  await expect(metadata.getByRole("rowheader")).toHaveCount(3);
  for (const value of [leftId, rightId, leftHash, rightHash, "81", "125"])
    await expect(metadata.getByText(value, { exact: true })).toBeVisible();
});

for (const viewport of [
  { name: "desktop", width: 1440, height: 980 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`resolution differences preserve database fields and missing values on ${viewport.name}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const longValue = "unknown-reference-0123456789".repeat(12);
    await mockKnowledge(page, true, (run) => {
      run.candidates[0].conflicts = [
        {
          field: "id",
          values: [
            { node_id: "n2", value: "000002" },
            { node_id: "n1", value: "000001" },
          ],
        },
        {
          field: "account_number",
          values: [
            { node_id: "n2", value: "0000456" },
            { node_id: "n1", value: "0000123" },
          ],
        },
        {
          field: "unknown_custom_field",
          values: [
            { node_id: "n1", value: longValue },
            { node_id: "n1", value: "补充来源值" },
            { node_id: "former-record", value: "旧成员的来源值" },
          ],
        },
        {
          field: "active",
          values: [
            { node_id: "n1", value: false },
            { node_id: "n2", value: true },
          ],
        },
        {
          field: "amount",
          values: [
            { node_id: "n1", value: 0 },
            { node_id: "n2", value: null },
          ],
        },
        {
          field: "empty_value",
          values: [
            { node_id: "n1", value: "" },
            { node_id: "n2", value: "有内容" },
          ],
        },
        {
          field: "@type",
          values: [
            { node_id: "n1", value: "客户" },
            { node_id: "n2", value: "机构" },
          ],
        },
      ];
    });
    await page.goto("/");
    await page.getByRole("tab", { name: "04 实体消歧" }).click();
    await page.getByRole("combobox", { name: "待消歧图谱" }).click();
    await page.getByText("数据库 · 客户数据库", { exact: true }).last().click();
    await page
      .getByRole("button", { name: "分析消歧候选", exact: true })
      .click();
    const differences = page.getByRole("region", { name: "差异对照" });
    await expect(
      differences.getByRole("heading", {
        name: "内容差异（7 项）",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      differences.getByRole("button", { name: /系统记录差异/ }),
    ).toHaveCount(0);
    const content = differences.getByRole("table", {
      name: "内容差异",
      exact: true,
    });
    for (const [field, leftValue, rightValue] of [
      ["id", "000001", "000002"],
      ["account_number", "0000123", "0000456"],
      ["unknown_custom_field", longValue, "未提供"],
      ["active", "否", "是"],
      ["amount", "0", "未提供"],
      ["empty_value", "空文本", "有内容"],
      ["实体类型", "客户", "机构"],
    ]) {
      const row = content.getByRole("row").filter({
        has: page.getByRole("rowheader", { name: field, exact: true }),
      });
      const cells = row.getByRole("cell");
      await expect(
        cells.nth(0).getByText(leftValue, { exact: true }),
      ).toBeVisible();
      await expect(
        cells.nth(1).getByText(rightValue, { exact: true }),
      ).toBeVisible();
    }
    const sourceValues = content.getByRole("row").filter({
      has: page.getByRole("rowheader", {
        name: "unknown_custom_field",
        exact: true,
      }),
    });
    await expect(sourceValues.getByRole("cell")).toHaveCount(3);
    await expect(
      sourceValues.getByRole("cell").nth(0).getByText("补充来源值", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      sourceValues.getByRole("cell").nth(2).getByText("旧成员的来源值", {
        exact: true,
      }),
    ).toBeVisible();
    if (viewport.name === "mobile") {
      const cells = content
        .getByRole("row")
        .filter({
          has: page.getByRole("rowheader", { name: "id", exact: true }),
        })
        .getByRole("cell");
      const left = await cells.nth(0).boundingBox();
      const right = await cells.nth(1).boundingBox();
      expect(left).not.toBeNull();
      expect(right).not.toBeNull();
      expect(right!.y).toBeGreaterThanOrEqual(left!.y + left!.height - 1);
      expect(
        await differences.evaluate(
          (node) => node.scrollWidth <= node.clientWidth + 1,
        ),
      ).toBe(true);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      ).toBe(true);
      await differences.screenshot({
        path: "test-results/resolution-differences-mobile.png",
        animations: "disabled",
      });
    }
  });
}

test("resolution output flows into matching and matching output stays selectable for resolution", async ({
  page,
}) => {
  const requests = await mockKnowledge(page);
  await page.goto("/");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  await page.getByRole("button", { name: "分析消歧候选" }).click();
  await page.getByRole("button", { name: "确认合并", exact: true }).click();
  await expect(page.getByText("3 → 2", { exact: true })).toBeVisible();
  expect(
    requests.find((item) => item.path.endsWith("/decisions"))?.body,
  ).not.toHaveProperty("reviewer");
  await page.getByRole("tab", { name: "03 GraphRAG 图谱与问答" }).click();
  await page.getByRole("tab", { name: "节点与边匹配", exact: true }).click();
  await page.getByRole("combobox", { name: "待匹配图谱版本" }).click();
  await page
    .getByText("企业关系.txt · 消歧结果", { exact: true })
    .last()
    .click();
  await page.getByRole("button", { name: "开始节点与边匹配" }).click();
  await expect(page.getByText("1 / 2", { exact: true })).toBeVisible();
  expect(
    requests.find((item) => item.path === "/knowledge/matches")?.body.source_id,
  ).toBe("resolution:resolution1:2");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  await page.getByRole("button", { name: "刷新来源" }).click();
  await page.getByRole("combobox", { name: "待消歧图谱" }).click();
  await page
    .getByText("GraphRAG · 企业关系.txt · 匹配结果", { exact: true })
    .last()
    .click();
  await page.getByRole("button", { name: "分析消歧候选" }).click();
  await expect(page.getByText("3 → 3", { exact: true })).toBeVisible();
  expect(
    requests.filter((item) => item.path === "/resolution/runs").at(-1)?.body
      .source_id,
  ).toBe("match:match1:1");
});

test("original graph traversal keeps the center and ranks its strongest neighbor", async () => {
  const { selectGraph } = await import("../src/features/knowledge/graphViews");
  const graph = structuredClone(originalGraph);
  graph.edges.push({
    id: "e2",
    source: "n1",
    target: "n2",
    properties: { weight: 9, combined_degree: 3 },
  });
  const before = JSON.stringify(graph);
  const ego = selectGraph(graph, "ego", "n1", 1, 2, []);
  expect(ego.nodes.map((n) => n.id)).toEqual(["n1", "n2"]);
  expect(ego.edges.map((e) => e.id)).toEqual(["e2"]);
  const evidence = {
    node_ids: ["n1", "n3"],
    edge_ids: ["e1"],
    cited_node_ids: ["n1"],
    cited_edge_ids: ["e1"],
  };
  expect(
    selectGraph(graph, "answer", "n1", 1, 50, [], evidence).edges.map(
      (e) => e.id,
    ),
  ).toEqual(["e1"]);
  expect(JSON.stringify(graph)).toEqual(before);
});

test("resolution method and review policy reach the analysis API", async ({
  page,
}) => {
  const requests = await mockKnowledge(page);
  await page.goto("/");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  await page.getByText("消歧方法与参数", { exact: true }).click();
  await page.getByRole("combobox", { name: "消歧方法", exact: true }).click();
  await page.getByText("原方法 · 同义词＋大模型", { exact: true }).click();
  await page
    .getByRole("spinbutton", { name: "判定调用预算", exact: true })
    .fill("2500");
  await page.getByRole("button", { name: "分析消歧候选", exact: true }).click();
  await expect
    .poll(
      () => requests.find((r) => r.path === "/resolution/runs")?.body.options,
    )
    .toMatchObject({
      method: "synonym_llm_v1",
      model_policy: "review",
      max_model_calls: 2500,
      retrieval_policy: "balanced",
      candidate_limit: 10,
      concurrency: 8,
    });
});

test("resolution previews completed candidates while review stays locked until finish", async ({
  page,
}) => {
  await mockKnowledge(page);
  let sourceCalls = 0;
  await page.route("**/resolution/runs/*/candidates/*/sources", (route) => {
    sourceCalls += 1;
    return route.fallback();
  });
  let complete = false;
  const preview: ResolutionRun = {
    id: "resolution1",
    name: "企业关系.txt",
    source_kind: "graphrag",
    source_id: "text1",
    revision: 0,
    status: "analyzing",
    created_at: "2026-09-21T01:00:00Z",
    error: null,
    progress: "候选比较 1/3 对；预计剩余约 1 分钟",
    summary: {
      original_node_count: 3,
      original_edge_count: 1,
      node_count: 3,
      edge_count: 1,
      pending_count: 1,
      merged_count: 0,
      rejected_count: 0,
    },
    candidates: [
      {
        id: "pair1",
        node_ids: ["n1", "n2"],
        nodes: originalGraph.nodes.slice(0, 2),
        score: 0.9,
        reasons: ["模型提出同一实体建议"],
        evidence: {
          origin: "model",
          verdict: "uncertain",
          proposal: "same",
          model_reason: "原文明确说明简称",
          left_quote: "甲企业提供服务",
          right_quote: "甲公司为甲企业简称",
        },
        conflicts: [],
        status: "pending",
        canonical_id: null,
      },
    ],
    audits: [],
    merges: [],
    diagnostics: {
      analysis: {
        completed: 1,
        total: 3,
        failed: 0,
        skipped: 0,
        eta_seconds: 60,
        preview_limit: 50,
        partial: true,
        model_budget: 2000,
      },
    },
  };
  let started = false;
  await page.route("**/api/v1/resolution/runs", (route) => {
    if (route.request().method() === "POST") {
      started = true;
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(preview),
      });
    }
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(started ? [preview] : []),
    });
  });
  await page.route("**/api/v1/resolution/runs/resolution1", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(
        complete
          ? {
              ...preview,
              status: "ready",
              diagnostics: {},
              progress: "候选分析完成",
            }
          : preview,
      ),
    }),
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  await page.getByRole("button", { name: "分析消歧候选", exact: true }).click();
  await expect(page.getByRole("tab", { name: "建议预览（1）" })).toBeVisible();
  await expect(page.getByText(/分析中预览：已处理 1\/3 对/)).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "甲企业 / 甲公司" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认合并", exact: true }),
  ).toBeDisabled();
  const sources = page.getByRole("region", { name: "原文与引用", exact: true });
  await expect(sources.getByText("分析完成后展示原文与引用")).toBeVisible();
  expect(sourceCalls).toBe(0);
  complete = true;
  await expect(page.getByRole("tab", { name: "合并建议（1）" })).toBeVisible({
    timeout: 10000,
  });
  await expect(
    page.getByRole("button", { name: "确认合并", exact: true }),
  ).toBeEnabled();
  await expect(sources.getByLabel("原文 n1")).toContainText("甲企业提供服务");
  await expect(sources.getByLabel("原文 n2")).toContainText(
    "甲公司为甲企业简称",
  );
  expect(sourceCalls).toBeGreaterThan(0);
  await page.getByRole("tab", { name: "分析记录（1）", exact: true }).click();
  await page
    .getByRole("combobox", { name: "模型判定筛选", exact: true })
    .click();
  await page.getByText("判为不同实体", { exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "甲企业 / 甲公司" }),
  ).toHaveCount(0);
});

for (const state of [
  "conflict",
  "failed",
  "different",
  "incomplete",
] as const) {
  test(`resolution ${state} is shown as an entity pair without a merge arrow`, async ({
    page,
  }) => {
    await mockKnowledge(page, true, (run) => {
      const row = run.candidates[0];
      row.status = "not_recommended";
      run.summary.pending_count = 0;
      run.summary.not_recommended_count = 1;
      if (state === "conflict" || state === "incomplete") {
        row.status = "excluded";
        run.summary.pending_count = 0;
        run.summary.excluded_count = 1;
        run.summary.not_recommended_count = 0;
      }
      row.nodes = [
        { id: "n1", name: "英伟达A100芯片", type: "产品或技术" },
        { id: "n2", name: "英伟达H100芯片", type: "产品或技术" },
      ];
      if (state === "incomplete")
        row.nodes = [
          { id: "n1", name: "科大讯飞2025年应收账款", type: "财务指标" },
          { id: "n2", name: "科大讯飞2025年9月末应收账款", type: "财务指标" },
        ];
      row.evidence =
        state === "conflict" || state === "incomplete"
          ? {
              proposal: "same",
              effective_verdict:
                state === "conflict" ? "different" : "uncertain",
              identity_guard: {
                verdict: state === "conflict" ? "different" : "uncertain",
                block_merge: true,
                message:
                  state === "conflict"
                    ? "身份字段冲突（产品型号或版本），应保留为不同实体"
                    : "身份限定不完整，缺少证明为同一实体的依据，暂不合并；保留独立节点",
                differences: [
                  {
                    field: state === "conflict" ? "产品型号或版本" : "观测时点",
                    left: state === "conflict" ? ["A100"] : [],
                    right: state === "conflict" ? ["H100"] : ["9月末"],
                  },
                ],
              },
            }
          : {
              verdict: state === "different" ? "different" : "uncertain",
              origin: state === "failed" ? "error" : "model",
            };
    });
    await page.goto("/");
    await page.getByRole("tab", { name: "04 实体消歧" }).click();
    await page
      .getByRole("button", { name: "分析消歧候选", exact: true })
      .click();
    await expect(
      page.getByRole("tab", { name: "合并建议（0）", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("暂无大模型建议合并的节点", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "确认合并", exact: true }),
    ).toHaveCount(0);
    await page.getByRole("tab", { name: "分析记录（1）", exact: true }).click();
    if (state === "conflict" || state === "incomplete") {
      await expect(
        page.getByRole("heading", {
          name:
            state === "conflict"
              ? "英伟达A100芯片 / 英伟达H100芯片"
              : "科大讯飞2025年应收账款 / 科大讯飞2025年9月末应收账款",
        }),
      ).toBeVisible();
      await page
        .getByRole("combobox", { name: "候选状态", exact: true })
        .click();
      await page.getByText("自动不合并", { exact: true }).last().click();
      await expect(
        page.getByText(
          state === "conflict"
            ? "已自动判定不合并，保留独立节点"
            : "身份依据不足，暂不合并，保留独立节点",
          {
            exact: true,
          },
        ),
      ).toBeVisible();
      await expect(page.getByLabel("已保留的独立实体")).toBeVisible();
      await expect(
        page.getByRole("button", { name: "确认合并", exact: true }),
      ).toHaveCount(0);
      await expect(
        page.getByRole("button", { name: "保留为不同实体", exact: true }),
      ).toHaveCount(0);
      await expect(
        page.getByRole("button", { name: "撤销审核决定", exact: true }),
      ).toHaveCount(0);
      await expect(
        page.getByRole("cell", {
          name: state === "conflict" ? "A100" : "未明确",
          exact: true,
        }),
      ).toBeVisible();
      await expect(
        page.getByRole("cell", {
          name: state === "conflict" ? "H100" : "9月末",
          exact: true,
        }),
      ).toBeVisible();
    } else if (state === "failed") {
      await expect(
        page.getByText("判断失败，尚未确认身份", { exact: true }),
      ).toBeVisible();
    } else if (state === "different") {
      await expect(
        page.getByText("判定为不同实体，尚未合并", { exact: true }),
      ).toBeVisible();
    }
    await expect(page.getByLabel("2 个节点合并为 1 个节点")).toHaveCount(0);
    if (state !== "conflict" && state !== "incomplete")
      await expect(page.getByLabel("待核验实体对，尚未合并")).toBeVisible();
  });
}

test("default confirmation queue contains only grounded model merge suggestions", async ({
  page,
}) => {
  await mockKnowledge(page, true, (run) => {
    const positive = run.candidates[0];
    const negative = ["different", "uncertain", "error"].map(
      (verdict, index) => ({
        ...structuredClone(positive),
        id: `filtered-${index}`,
        score: 0.999,
        status: "not_recommended" as const,
        nodes: positive.nodes.map((node) => ({
          ...node,
          name: `${verdict}-${node.name}`,
        })),
        evidence: {
          origin: verdict === "error" ? "error" : "model",
          verdict: verdict === "error" ? "uncertain" : verdict,
        },
      }),
    );
    run.candidates = [negative[0], positive, ...negative.slice(1)];
    run.summary.pending_count = 1;
    run.summary.not_recommended_count = 3;
  });
  await page.goto("/");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  await page.getByRole("button", { name: "分析消歧候选", exact: true }).click();
  await expect(
    page.getByRole("tab", { name: "合并建议（1）", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "甲企业 / 甲公司", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("原文明确说明甲公司为甲企业简称", { exact: true }),
  ).toBeVisible();
  for (const value of ["different", "uncertain", "error"]) {
    await expect(
      page.getByRole("heading", {
        name: `${value}-甲企业 / ${value}-甲公司`,
        exact: true,
      }),
    ).toHaveCount(0);
  }
  await expect(
    page.getByRole("button", { name: "确认合并", exact: true }),
  ).toHaveCount(1);
  await page
    .getByRole("button", { name: "大模型引用与来源", exact: true })
    .click();
  await expect(page.getByText(/甲公司为甲企业简称/).last()).toBeVisible();
  await page.getByRole("tab", { name: "分析记录（4）", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: "different-甲企业 / different-甲公司",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("combobox", { name: "候选状态", exact: true }).click();
  await page.getByText("未建议合并", { exact: true }).last().click();
  await expect(
    page.getByRole("button", { name: "确认合并", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "合并建议（1）", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "甲企业 / 甲公司", exact: true }),
  ).toBeVisible();
});

test("generated node attributes are not presented as document quotations", async ({
  page,
}) => {
  await mockKnowledge(page, true, (run) => {
    const row = run.candidates[0];
    row.status = "not_recommended";
    row.evidence.quote_validation = {
      supported: false,
      left: {
        origin: "node_attribute",
        fields: ["title"],
        message: "仅在抽取后的节点属性中找到，未在该节点关联的原文中找到",
      },
      right: {
        origin: "source_text",
        message: "引用可在该节点关联的原文片段中逐字找到",
        excerpt: "测试文档记载：甲公司为甲企业简称。",
        text_unit_ids: ["source-unit-1"],
      },
    };
    run.summary.pending_count = 0;
    run.summary.not_recommended_count = 1;
  });
  await page.goto("/");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  await page.getByRole("button", { name: "分析消歧候选", exact: true }).click();
  await expect(
    page.getByText("暂无大模型建议合并的节点", { exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "分析记录（1）", exact: true }).click();
  await expect(
    page.getByText("模型引用未通过来源校验", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "大模型引用与来源", exact: true })
    .click();
  await expect(
    page.getByText(/GraphRAG 抽取后的节点属性（不是文档原文）/),
  ).toBeVisible();
  await expect(
    page.getByText(/测试文档记载：甲公司为甲企业简称/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认合并", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("冲突属性", { exact: true })).toHaveCount(0);
});

test("source panel automatically loads full original text, retries and expands without reloading", async ({
  page,
}) => {
  await mockKnowledge(page);
  let calls = 0;
  let failSources = true;
  await page.route("**/resolution/runs/*/candidates/*/sources", (route) => {
    calls += 1;
    if (failSources)
      return route.fulfill({
        status: 503,
        json: { detail: "原文加载失败，请重试" },
      });
    return route.fulfill({
      json: {
        run_id: "resolution1",
        candidate_id: "candidate1",
        source_name: "企业关系.txt",
        source_kind: "graphrag",
        nodes: [
          {
            node_id: "n1",
            name: "甲企业",
            quote: "甲企业全称",
            quote_location: {
              origin: "node_attribute",
              message: "引用仅命中 title，未在关联原文中找到",
              fields: ["title"],
            },
            records: [
              {
                node_id: "n1",
                name: "甲企业",
                description: "这是模型生成的描述",
                source_text:
                  "原文第一行：BIS发布规则。\n原文最后一行：保留完整段落。<script>literal text</script>",
                text_unit_ids: ["unit-left"],
                fields: null,
              },
            ],
          },
          {
            node_id: "n2",
            name: "甲公司",
            quote: "甲公司为甲企业简称",
            quote_location: {
              origin: "source_text",
              message: "引用在关联原文中逐字找到",
            },
            records: [
              {
                node_id: "n2",
                name: "甲公司",
                description: "模型描述不替代原文",
                source_text:
                  "右侧原文：甲公司为甲企业简称。此后仍有完整上下文。",
                text_unit_ids: ["unit-right"],
                fields: null,
              },
            ],
          },
        ],
      },
    });
  });
  await page.goto("/");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  await page.getByRole("button", { name: "分析消歧候选", exact: true }).click();
  const sources = page.getByRole("region", { name: "原文与引用", exact: true });
  await expect(sources).toBeVisible();
  await expect(sources.getByText("原文加载失败，请重试")).toBeVisible();
  await expect(
    sources.getByRole("button", { name: "展开阅读" }),
  ).toBeDisabled();
  const failedCalls = calls;
  expect(failedCalls).toBeGreaterThan(0);
  failSources = false;
  await sources.getByRole("button", { name: "重新加载" }).click();
  const left = sources.getByRole("region", { name: "来源节点 1" });
  const right = sources.getByRole("region", { name: "来源节点 2" });
  await expect(left.getByText("实际命中的节点属性：title")).toBeVisible();
  expect(calls).toBeGreaterThan(failedCalls);
  await expect(left.getByLabel("原文 n1")).toContainText(
    "原文最后一行：保留完整段落。<script>literal text</script>",
  );
  await expect(right.getByLabel("原文 n2")).toContainText(
    "此后仍有完整上下文。",
  );
  await expect(left.locator("mark")).toHaveCount(0);
  await expect(right.locator("mark")).toHaveText("甲公司为甲企业简称");
  await expect(left.locator("script")).toHaveCount(0);
  await left
    .getByRole("button", { name: "节点 description（抽取后的描述，不是原文）" })
    .click();
  await expect(
    left.getByText("这是模型生成的描述", { exact: true }),
  ).toBeVisible();
  await left.getByRole("button", { name: "原文分块 ID" }).click();
  await expect(left.getByText(/unit-left/)).toBeVisible();
  const loadedCalls = calls;
  await sources.getByRole("button", { name: "展开阅读" }).click();
  const dialog = page.getByRole("dialog", { name: "消歧依据 · 原文与引用" });
  await expect(dialog.getByLabel("原文 n1")).toHaveText(
    await left.getByLabel("原文 n1").innerText(),
  );
  await expect(dialog.getByLabel("原文 n2").locator("mark")).toHaveText(
    "甲公司为甲企业简称",
  );
  expect(calls).toBe(loadedCalls);
});

test("source panel identifies database fields without inventing document text", async ({
  page,
}) => {
  await mockKnowledge(page);
  await page.route("**/resolution/runs/*/candidates/*/sources", (route) =>
    route.fulfill({
      json: {
        run_id: "resolution1",
        candidate_id: "candidate1",
        source_name: "数据库图谱",
        source_kind: "database",
        nodes: [
          {
            node_id: "n1",
            name: "甲企业",
            quote: "0000123",
            quote_location: {
              origin: "source_record",
              message: "引用来自数据库来源记录的字段",
            },
            records: [
              {
                node_id: "n1",
                name: "甲企业",
                description: null,
                source_text: null,
                text_unit_ids: [],
                fields: { account: "0000123" },
              },
            ],
          },
        ],
      },
    }),
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  await page.getByRole("button", { name: "分析消歧候选", exact: true }).click();
  const sources = page.getByRole("region", { name: "原文与引用", exact: true });
  await expect(
    sources.getByRole("heading", { name: "数据库来源字段", exact: true }),
  ).toBeVisible();
  await expect(
    sources.getByRole("heading", { name: "文档原文", exact: true }),
  ).toHaveCount(0);
  await expect(sources.getByLabel("来源字段 n1").locator("mark")).toHaveText(
    "0000123",
  );
});

test("source panel shows missing text explicitly and keeps long content within a mobile viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const longId = "abcdef0123456789".repeat(20);
  await mockKnowledge(page, true, (run) => {
    run.candidates[0].conflicts.push({
      field: "text_unit_ids",
      values: [
        { node_id: "n1", value: [longId] },
        { node_id: "n2", value: [`${longId}-right`] },
      ],
    });
  });
  await page.route("**/resolution/runs/*/candidates/*/sources", (route) =>
    route.fulfill({
      json: {
        run_id: "resolution1",
        candidate_id: "candidate1",
        source_name: "企业关系.txt",
        source_kind: "graphrag",
        nodes: [
          {
            node_id: "n1",
            name: "甲企业",
            quote: "甲企业全称",
            quote_location: {
              origin: "node_attribute",
              message: "引用仅命中节点属性，关联原文缺失",
            },
            records: [
              {
                node_id: "n1",
                name: "甲企业",
                description: "这段抽取描述不能作为缺失原文的替代品",
                source_text: null,
                text_unit_ids: [longId],
                fields: null,
              },
            ],
          },
          {
            node_id: "n2",
            name: "甲公司",
            quote: "甲公司为甲企业简称",
            quote_location: {
              origin: "source_text",
              message: "引用可在关联原文中逐字找到",
            },
            records: [
              {
                node_id: "n2",
                name: "甲公司",
                description: null,
                source_text: `甲公司为甲企业简称。\n${longId}\n完整原文的最后一句。`,
                text_unit_ids: [`${longId}-right`],
                fields: null,
              },
            ],
          },
        ],
      },
    }),
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  await page.getByRole("button", { name: "分析消歧候选", exact: true }).click();
  const sources = page.getByRole("region", { name: "原文与引用", exact: true });
  const left = sources.getByRole("region", { name: "来源节点 1" });
  const right = sources.getByRole("region", { name: "来源节点 2" });
  await expect(
    left.getByText("此记录未保存文档原文，不能用节点名称或 description 代替。"),
  ).toBeVisible();
  await expect(left.getByLabel("原文 n1")).toHaveCount(0);
  await expect(right.getByLabel("原文 n2")).toContainText(
    "完整原文的最后一句。",
  );
  await left.getByRole("button", { name: "原文分块 ID" }).click();
  await expect(left.getByText(new RegExp(longId))).toBeVisible();
  for (const element of [sources, left, right]) {
    expect(
      await element.evaluate(
        (node) => node.scrollWidth <= node.clientWidth + 1,
      ),
    ).toBe(true);
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
});
