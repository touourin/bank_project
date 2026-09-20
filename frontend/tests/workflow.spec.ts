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
  await page.route("**/api/v1/alignment/graph", (route) =>
    route.fulfill({ json: { summary: null, nodes: [], edges: [] } }),
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "02 本体对齐与图谱生成" }).click();
  await expect(
    page.getByRole("heading", { name: "当前分析任务", exact: true }),
  ).toBeVisible();
}

test("simple rules are reviewable without advanced configuration and require confirmation", async ({
  page,
}) => {
  const run = exampleRun();
  run.result!.template!.confirmed = false;
  await showRun(page, run);
  const generate = page.getByRole("button", { name: "生成图谱", exact: true });
  await expect(generate).toBeDisabled();
  await expect(page.locator(".rule-card")).toContainText("客户");
  await expect(page.locator(".rule-card")).toContainText("按 id 识别");
  await expect(
    page.getByRole("textbox", { name: "节点 1 身份范围", exact: true }),
  ).not.toBeVisible();
  await expect(page.locator(".generation-metrics")).toContainText("1,000,000");
  let sent: unknown;
  const saved = {
    ...run,
    id: "rules-confirmed",
    based_on_run_id: run.id,
    result: {
      ...run.result!,
      template: { ...run.result!.template!, confirmed: true },
    },
  };
  await page.route(
    `**/api/v1/alignment/runs/${run.id}/template`,
    async (route) => {
      sent = route.request().postDataJSON();
      await route.fulfill({ status: 201, json: saved });
    },
  );
  await page.route("**/api/v1/alignment/runs/rules-confirmed", (route) =>
    route.fulfill({ json: saved }),
  );
  await page.getByRole("button", { name: "确认生成规则", exact: true }).click();
  await expect(generate).toBeEnabled();
  expect(sent).toEqual(run.result!.template);
  await generate.click();
  const dialog = page.getByRole("dialog", { name: "生成图谱版本" });
  await expect(dialog).toContainText("客户 · 1,000,000 行");
  await expect(dialog).toContainText("实际节点与关系数量在生成后统计");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.screenshot({
    path: "test-results/workflow-simple.png",
    fullPage: true,
    animations: "disabled",
  });
});

test("multiple-object warning survives incomplete matching and requires renewed review after edits", async ({
  page,
}) => {
  const run = exampleRun();
  run.result!.template!.confirmed = false;
  run.result!.tables[0].structure_notes = [
    "分组匹配不完整，当前仅保留整表草稿，请核对客户、账户。",
  ];
  await showRun(page, run);
  const confirm = page.getByRole("button", {
    name: "确认生成规则",
    exact: true,
  });
  await expect(
    page.getByText("这张表可能包含多个对象，请先核对分组", { exact: true }),
  ).toBeVisible();
  await expect(confirm).toBeDisabled();
  const review = page.getByRole("checkbox", {
    name: "我已核对多个对象的字段归属、身份标识及关系依据",
  });
  await review.check();
  await expect(confirm).toBeEnabled();
  await page
    .getByText("高级配置：实体拆分、跨表合并与关系", { exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "节点 1 身份范围", exact: true })
    .fill("shared-customers");
  await expect(review).not.toBeChecked();
  await expect(confirm).toBeDisabled();
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
  await page.route("**/api/v1/alignment/graph", (route) =>
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
    page.getByRole("button", { name: "生成图谱", exact: true }),
  ).not.toBeVisible();
  run.status = "ready";
  run.progress = "分析完成"; // Older runs may have a misleading generic completion message.
  run.result!.tables[0].status = "failed";
  run.result!.tables[0].reason =
    "第 7/42 批（第 193–224 列）失败：模型请求失败或超时";
  run.result!.tables[0].trace!.steps[0].status = "failed";
  await page.reload();
  await page.getByRole("tab", { name: "02 本体对齐与图谱生成" }).click();
  await expect(page.locator(".run-progress")).toContainText("1/1 张表失败");
  await expect(page.locator(".task-source-error")).toContainText(
    "第 193–224 列",
  );
  await expect(page.locator(".run-progress")).not.toContainText("规则已就绪");
});
