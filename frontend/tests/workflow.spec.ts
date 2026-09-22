import { expect, test, type Page } from "@playwright/test";
import { exampleRun } from "./helpers/alignment";
import type { Run } from "../src/features/alignment/types";

async function showRun(page: Page, run: Run) {
  await page.route("**/api/v1/alignment/runs", (route) =>
    route.fulfill({ json: [{ ...run, result: null }] }),
  );
  await page.route(`**/api/v1/alignment/runs/${run.id}`, (route) =>
    route.fulfill({ json: run }),
  );
  await page.route("**/api/v1/alignment/graph/overview", (route) =>
    route.fulfill({ json: { summary: null, nodes: [], edges: [] } }),
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "02 数据转换" }).click();
  await expect(
    page.getByRole("heading", { name: "当前分析任务", exact: true }),
  ).toBeVisible();
}

test("complete proposal is adopted in one action without individual confirmation", async ({
  page,
}) => {
  const run = exampleRun();
  run.result!.template!.confirmed = false;
  run.result!.tables[0].source_name = "客户.xlsx";
  run.result!.tables[0].status = "review";
  run.result!.tables[0].confidence = 0.327;
  await showRun(page, run);
  const generate = page.getByRole("button", {
    name: "采纳方案并生成",
    exact: true,
  });
  await expect(generate).toBeEnabled();
  await expect(page.locator(".rule-card")).toContainText("客户.xlsx / 客户");
  await expect(page.locator(".rule-card")).toContainText("按 id 识别");
  await expect(page.getByRole("checkbox", { name: /我已核对/ })).toHaveCount(0);
  await expect(page.locator(".generation-metrics")).toContainText("1,000,000");
  let sent: unknown;
  const saved = {
    ...run,
    id: "adopted",
    based_on_run_id: run.id,
    graph_status: "building",
  };
  await page.route(
    `**/api/v1/alignment/runs/${run.id}/graph`,
    async (route) => {
      sent = route.request().postDataJSON();
      await route.fulfill({ status: 202, json: saved });
    },
  );
  await page.route("**/api/v1/alignment/runs/adopted", (route) =>
    route.fulfill({ json: saved }),
  );
  await generate.click();
  const dialog = page.getByRole("dialog", { name: "采纳匹配方案并生成图谱" });
  await expect(dialog).toContainText("客户.xlsx / 客户 · 1,000,000 行");
  await expect(dialog).toContainText("低分或未验证的对象建议");
  await dialog
    .getByRole("button", { name: "采纳并开始生成", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  expect(sent).toEqual(run.result!.template);
  await expect(page.locator(".run-progress")).toContainText("正在生成图谱");
});

test("grouping warnings stay visible but do not require per-item review", async ({
  page,
}) => {
  const run = exampleRun();
  run.result!.template!.confirmed = false;
  run.result!.tables[0].structure_notes = [
    "分组匹配不完整，当前保留整表草稿，请核对客户、账户。",
  ];
  await showRun(page, run);
  const generate = page.getByRole("button", {
    name: "采纳方案并生成",
    exact: true,
  });
  await expect(page.getByText("对象分组说明", { exact: true })).toBeVisible();
  await expect(generate).toBeEnabled();
  await expect(page.getByRole("checkbox", { name: /我已核对/ })).toHaveCount(0);
  await page
    .getByText("高级配置：实体拆分、跨表合并与关系", { exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "节点 1 身份范围", exact: true })
    .fill("shared-customers");
  await expect(generate).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "刷新任务", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "撤销未保存修改", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "节点 1 身份范围", exact: true }),
  ).toHaveValue("customers");
  await expect(
    page.getByRole("button", { name: "刷新任务", exact: true }),
  ).toBeEnabled();
  await page.screenshot({
    path: "test-results/proposal-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
});

test("published status, current graph origin and newly selected sources stay distinct", async ({
  page,
}) => {
  const run = exampleRun();
  run.graph_status = "ready";
  run.graph_version = "published-version";
  run.progress = "人工修改已保存，请核对后生成图谱";
  run.based_on_run_id = "original-task";
  await showRun(page, run);
  await expect(
    page.getByText("人工修改已保存，请核对后生成图谱", { exact: true }),
  ).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "该版本已生成图谱", exact: true }),
  ).toBeDisabled();
  await page.route("**/api/v1/alignment/graph/overview", (route) =>
    route.fulfill({
      json: {
        summary: {
          version: "other-version",
          run_id: "different-task",
          revision: "r1",
          node_count: 2,
          edge_count: 0,
          created_at: run.created_at,
        },
        nodes: [],
        edges: [],
      },
    }),
  );
  await page.getByRole("button", { name: "刷新图谱", exact: true }).click();
  await expect(
    page.getByText("此图谱来自另一条任务", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".graph-metrics")).toContainText("2 个实例");
  await expect(page.locator(".task-sources")).toContainText("客户");
  await page.route("**/api/v1/intake/batches?*", (route) =>
    route.fulfill({
      json: {
        total: 1,
        items: [
          {
            id: "new-batch",
            name: "新交易.csv",
            created_at: run.created_at,
            row_count: 20,
            table_count: 1,
          },
        ],
      },
    }),
  );
  await page.route("**/api/v1/intake/batches/new-batch", (route) =>
    route.fulfill({
      json: {
        tables: [
          {
            id: "new-table",
            name: "新交易",
            row_count: 20,
            columns: [{ name: "流水号" }],
          },
        ],
      },
    }),
  );
  await page.getByRole("button", { name: "刷新列表", exact: true }).click();
  await page
    .locator(".alignment-batch-name")
    .filter({ hasText: "新交易.csv" })
    .click();
  await page.locator(".alignment-tables").getByRole("checkbox").focus();
  await page.keyboard.press("Space");
  await expect(
    page.getByText("左侧选择与当前任务不同", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".task-sources")).toContainText("客户");
  await expect(page.locator(".task-sources")).not.toContainText("新交易");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/workflow-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
});

test("wide table progress and failed batches stay visible in the current task", async ({
  page,
}) => {
  const run = exampleRun();
  run.status = "analyzing";
  run.progress =
    "字段解释已完成 6/42 批（192/1,334 列），最多 3 批并发；随后执行 retrieve 匹配";
  run.result!.tables[0].trace = {
    method: "retrieve",
    retrievals: [],
    sample_rows: [1],
    meaning: null,
    candidates: [],
    attribute_candidates: [],
    selected: null,
    selection_reason: "",
    selection_confidence: null,
    verification: "none",
    selection_attempts: 0,
    confidence_threshold: 0.75,
    steps: [{ key: "meaning", status: "running", detail: run.progress }],
  };
  await showRun(page, run);
  await expect(page.locator(".task-sources")).toContainText(
    "6/42 批（192/1,334 列）",
  );
  await expect(
    page.getByRole("button", { name: "采纳方案并生成", exact: true }),
  ).not.toBeVisible();
  run.status = "ready";
  run.progress = "分析完成"; // Older runs may have a misleading generic completion message.
  run.result!.tables[0].status = "failed";
  run.result!.tables[0].reason =
    "第 7/42 批（第 193–224 列）失败：模型请求失败或超时";
  run.result!.tables[0].trace!.steps[0].status = "failed";
  await page.reload();
  await page.getByRole("tab", { name: "02 数据转换" }).click();
  await expect(page.locator(".run-progress")).toContainText("1/1 张表失败");
  await expect(page.locator(".task-source-error")).toContainText(
    "第 193–224 列",
  );
  await expect(page.locator(".run-progress")).not.toContainText("规则已就绪");
});

test("same concept in different roles keeps each retrieval score and excluded sheets show filenames", async ({
  page,
}) => {
  const run = exampleRun();
  const table = run.result!.tables[0];
  const candidate = { id: "customer", name: "客户", parents: [], score: 1 };
  table.trace = {
    method: "retrieve",
    retrievals: [
      {
        target: "entity",
        name: "buyer",
        query: "买方",
        status: "matched",
        candidates: [candidate],
        selected: candidate,
        confident: true,
        match_method: "exact",
        detail: "精确命中",
      },
      {
        target: "entity",
        name: "seller",
        query: "卖方",
        status: "review",
        candidates: [{ ...candidate, score: 0.327 }],
        selected: { ...candidate, score: 0.327 },
        confident: false,
        match_method: "vector",
        detail: "候选得分较低",
      },
    ],
    sample_rows: [1],
    meaning: null,
    candidates: [],
    attribute_candidates: [],
    selected: null,
    selection_reason: "",
    selection_confidence: null,
    verification: "verified",
    selection_attempts: 0,
    confidence_threshold: 0.75,
    steps: [],
  };
  const node = run.result!.template!.nodes[0];
  run.result!.template!.nodes = [
    {
      ...node,
      id: "buyer",
      retrieval_target: "entity",
      retrieval_name: "buyer",
    },
    {
      ...node,
      id: "seller",
      retrieval_target: "entity",
      retrieval_name: "seller",
    },
  ];
  run.result!.tables.push({
    ...table,
    table_id: "t2",
    table_name: "数据",
    source_name: "客户旅程.xlsx",
    status: "unmatched",
    concept_id: null,
    verification: "verified",
  });
  run.result!.tables.push({
    ...table,
    table_id: "t3",
    table_name: "数据",
    source_name: "工商.xlsx",
    status: "review",
    verification: "unavailable",
  });
  await showRun(page, run);
  await expect(page.locator(".rule-card").nth(0)).toContainText(
    "自动匹配 · 1.000",
  );
  await expect(page.locator(".rule-card").nth(1)).toContainText(
    "候选建议 · 0.327",
  );
  await expect(
    page.getByText("包含 1 个低分或未验证的对象建议", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "客户旅程.xlsx / 数据：没有有效对象候选，请重新分析或手动选择类型",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByText(
      "工商.xlsx / 数据：对象检索未完成，请重新分析或手动选择类型",
      { exact: true },
    ),
  ).toBeVisible();
});

test("failed field retrieval defaults to a raw attribute while preserving failure and later manual choices", async ({
  page,
}) => {
  const run = exampleRun();
  const table = run.result!.tables[0];
  table.trace = {
    method: "retrieve",
    retrievals: [
      {
        target: "column",
        name: "id",
        query: "实际控制人客户编号",
        status: "unavailable",
        candidates: [],
        selected: null,
        confident: false,
        match_method: "none",
        detail: "检索接口暂时不可用",
      },
      {
        target: "entity",
        name: "controller",
        query: "实际控制人",
        status: "unmatched",
        candidates: [],
        selected: null,
        confident: false,
        match_method: "none",
        detail: "无候选",
      },
    ],
    sample_rows: [1],
    meaning: null,
    candidates: [],
    attribute_candidates: [],
    selected: null,
    selection_reason: "",
    selection_confidence: null,
    verification: "verified",
    selection_attempts: 0,
    confidence_threshold: 0.75,
    steps: [],
  };
  await showRun(page, run);
  await expect(page.getByRole("region", { name: /字段映射/ })).toContainText(
    "默认属性",
  );
  await page.getByRole("tab", { name: "匹配过程", exact: true }).click();
  const records = page.getByRole("region", {
    name: "retrieve 匹配记录",
    exact: true,
  });
  const field = records
    .locator(".ant-table-row")
    .filter({ hasText: "实际控制人客户编号" });
  await expect(field).toContainText("检索失败");
  await expect(field).toContainText("默认属性");
  await expect(records.getByText("默认属性", { exact: true })).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "采纳方案并生成", exact: true }),
  ).toBeEnabled();
  // A subsequent manual mapping changes the handling, but never rewrites retrieval history.
  table.columns[0].concept_id = "customer";
  table.columns[0].concept_name = "客户";
  await page.reload();
  await page.getByRole("tab", { name: "02 数据转换" }).click();
  await page.getByRole("tab", { name: "匹配过程", exact: true }).click();
  await expect(field).toContainText("检索失败");
  await expect(field).toContainText("已指定节点");
  await expect(field).not.toContainText("默认属性");
});

test("default plan replaces grouping with all fields per row and can be restored", async ({
  page,
}) => {
  const run = exampleRun();
  const original = structuredClone(run.result!.template!);
  run.result!.tables[0].structure_notes = [
    "部分字段无法确定归属，请核对分组。",
  ];
  run.result!.tables[0].columns.push({
    ...run.result!.tables[0].columns[0],
    column: "name",
    semantic: "name",
    property_key: "field_001",
  });
  const preset = {
    ...original,
    mode: "row_records",
    confirmed: false,
    edges: [],
    nodes: [
      {
        ...original.nodes[0],
        id: "t1",
        identity_scope: "row:t1",
        key_columns: [],
        properties: [
          { column: "id", name: "id" },
          { column: "name", name: "name" },
        ],
      },
    ],
  };
  let attempts = 0;
  await page.route(
    `**/api/v1/alignment/runs/${run.id}/template/default`,
    async (route) => {
      attempts++;
      expect(route.request().postDataJSON().nodes[0].concept_id).toBe(
        "customer",
      );
      if (attempts === 1)
        await route.fulfill({
          status: 503,
          json: { detail: "服务暂时不可用" },
        });
      else await route.fulfill({ json: preset });
    },
  );
  await showRun(page, run);
  const defaults = page.getByRole("button", {
    name: "使用默认方案",
    exact: true,
  });
  await defaults.click();
  await expect(page.getByText("服务暂时不可用", { exact: true })).toBeVisible();
  await expect(page.locator(".rule-card")).toContainText("按 id 识别");
  await defaults.click();
  await expect(
    page.getByText("已使用默认方案，可直接采纳并生成", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".rule-card")).toContainText(
    "每条来源记录单独生成实例",
  );
  await expect(page.locator(".rule-card")).toContainText("写入 2 个属性");
  await expect(
    page.getByText("对象分组说明", { exact: true }),
  ).not.toBeVisible();
  await expect(
    page.getByText("部分字段无法确定归属，请核对分组。", { exact: false }),
  ).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "采纳方案并生成", exact: true }),
  ).toBeEnabled();
  // Switching source workflows preserves the unsaved table template.
  await page.getByRole("tab", { name: "TXT 文本", exact: true }).click();
  await page.getByRole("tab", { name: "表格 / MySQL", exact: true }).click();
  await expect(page.locator(".rule-card")).toContainText(
    "每条来源记录单独生成实例",
  );
  await page.getByRole("button", { name: "恢复之前方案", exact: true }).click();
  await expect(page.locator(".rule-card")).toContainText("按 id 识别");
  await defaults.click();
  await page.screenshot({
    path: "test-results/default-plan.png",
    fullPage: true,
    animations: "disabled",
  });
  let submitted:
    | {
        mode: string;
        nodes: { key_columns: string[]; properties: unknown[] }[];
        edges: unknown[];
      }
    | undefined;
  await page.route(
    `**/api/v1/alignment/runs/${run.id}/graph`,
    async (route) => {
      submitted = route.request().postDataJSON();
      await route.fulfill({ json: { ...run, graph_status: "building" } });
    },
  );
  await page
    .getByRole("button", { name: "采纳方案并生成", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "采纳匹配方案并生成图谱" });
  await dialog
    .getByRole("button", { name: "采纳并开始生成", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  expect(submitted?.mode).toBe("row_records");
  expect(submitted?.nodes[0].key_columns).toEqual([]);
  expect(submitted?.nodes[0].properties).toHaveLength(2);
  expect(submitted?.edges).toEqual([]);
});
