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

async function mockKnowledge(page: Page, initial = true) {
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
      if (path === "/graphrag/datasets/text1/query")
        return send({
          answer: `${body.method}：甲企业向乙企业提供服务。`,
          context: {
            entities: [{ id: "n1", title: "甲企业" }],
            sources: ["企业关系.txt"],
          },
          index_basis: "original_graphrag_index",
        });
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
              candidates: [
                { id: "BO_CORP", name: "对公客户", score: 0.98, parents: [] },
                { id: "BO_ORG", name: "组织", score: 0.61, parents: [] },
              ],
              selected: { id: "BO_CORP", name: "对公客户", parents: [] },
              confident: true,
              match_method: "retrieve",
              detail: "复用 retrieve 候选，得分 0.980",
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
        if (body.target === "node")
          match.nodes.find((node) => node.id === body.target_id)!.boid =
            body.boid as string | null;
        else
          match.edges.find((edge) => edge.id === body.target_id)!.edge_type =
            body.edge_type as string | null;
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
              evidence: { documents: ["企业关系.txt"], method: "alias" },
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
        return send(resolution, 202);
      }
      if (/^\/resolution\/runs\/resolution\d+$/.test(path))
        return send(resolution);
      if (/^\/resolution\/runs\/resolution\d+\/graph$/.test(path))
        return send(derivedGraph);
      if (
        (path.endsWith("/decisions") || path.endsWith("/manual")) &&
        resolution
      ) {
        expect(body.expected_revision).toBe(resolution.revision);
        resolution.revision++;
        const candidate = resolution.candidates[0];
        const action = path.endsWith("/manual") ? "merge" : body.action;
        candidate.status =
          action === "merge"
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

test("TXT ingestion, graph details and all three query methods", async ({
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
  ]) {
    await page.getByRole("combobox", { name: "检索方式" }).click();
    await page.getByText(label, { exact: true }).last().click();
    await page.getByRole("button", { name: "查询图谱", exact: true }).click();
    await expect(page.locator(".knowledge-answer")).toContainText(
      `${method}：`,
    );
  }
  await page.getByText("检索证据与上下文", { exact: true }).click();
  await expect(
    page.locator(".knowledge-json").filter({ hasText: "sources" }),
  ).toContainText("企业关系.txt");
  expect(
    requests
      .filter((item) => item.path.endsWith("/query"))
      .map((item) => item.body.method),
  ).toEqual(["local", "global", "drift"]);
});

test("GraphRAG matching exposes retrieval scores, manual annotations and original fields", async ({
  page,
}) => {
  const requests = await mockKnowledge(page);
  await page.goto("/");
  await page.getByRole("tab", { name: "03 GraphRAG 图谱与问答" }).click();
  await page.getByRole("tab", { name: "节点与边匹配", exact: true }).click();
  await page.getByRole("button", { name: "开始节点与边匹配" }).click();
  await page
    .getByRole("button", { name: "展开行", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("cell", { name: "0.980", exact: true }),
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
  await expect(
    page.getByRole("cell", { name: "BO_ORG", exact: true }).first(),
  ).toBeVisible();
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

test("both graph sources support review, visible merge flow, undo and manual merge", async ({
  page,
}) => {
  const requests = await mockKnowledge(page);
  await page.goto("/");
  await page.getByRole("tab", { name: "04 实体消歧" }).click();
  for (const source of ["GraphRAG · 企业关系.txt", "数据库 · 客户数据库"]) {
    await page.getByRole("combobox", { name: "待消歧图谱" }).click();
    await page.getByText(source, { exact: true }).last().click();
    await page.getByRole("button", { name: "分析消歧候选" }).click();
    await expect(page.getByText("存在 1 项属性冲突，请核验")).toBeVisible();
    await expect(page.getByLabel("2 个节点合并为 1 个节点")).toBeVisible();
    await page.getByLabel("消歧审核人").fill("李审核");
    await page.getByLabel("消歧审核备注").fill("已核实为同一企业");
    await page.getByRole("button", { name: "确认合并", exact: true }).click();
    await expect(page.getByText("3 → 2", { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "合并过程（1）", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "已合并 2 个实体" }),
    ).toBeVisible();
    if (source.startsWith("GraphRAG")) {
      await page.getByRole("button", { name: "切换深浅主题" }).click();
      await page.screenshot({
        path: "../outputs/resolution-review-fixture.png",
        fullPage: true,
        animations: "disabled",
      });
    }
    await page.getByRole("tab", { name: "审核历史（1）", exact: true }).click();
    await expect(
      page.getByRole("cell", { name: "李审核", exact: true }),
    ).toBeVisible();
    await page.getByRole("tab", { name: "候选核验（1）", exact: true }).click();
    await page.getByRole("combobox", { name: "候选状态" }).click();
    await page.getByText("已合并", { exact: true }).last().click();
    await page.getByRole("button", { name: "撤销审核决定" }).click();
    await expect(page.getByText("3 → 3", { exact: true })).toBeVisible();
  }
  await page.getByRole("button", { name: "人工指定合并" }).click();
  const dialog = page.getByRole("dialog");
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
    expected_revision: 3,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/resolution-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
});

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
