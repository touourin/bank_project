import { expect, test } from "@playwright/test";

test("one-step generation includes unsaved edits and preserves them after failure", async ({
  page,
}) => {
  const run = {
    id: "template-original",
    created_at: "2026-09-19T00:00:00Z",
    status: "ready",
    progress: "分析完成",
    error: null,
    graph_status: "none",
    graph_error: null,
    graph_version: null,
    result: {
      revision: "r1",
      snapshot_sha256: "test",
      warnings: [],
      relations: [],
      tables: [
        {
          table_id: "t1",
          batch_id: "b1",
          table_name: "客户",
          row_count: 1000000,
          concept_id: "customer",
          concept_name: "客户",
          confidence: 0.9,
          status: "mapped",
          verification: "local_catalog",
          reason: "客户表",
          warnings: [],
          columns: [
            {
              column: "id",
              role: "attribute",
              semantic: "id",
              concept_id: null,
              concept_name: null,
              property_key: "field_000",
              reason: "编号",
            },
          ],
        },
      ],
      template: {
        confirmed: true,
        note: "",
        edges: [],
        nodes: [
          {
            id: "n1",
            table_id: "t1",
            concept_id: "customer",
            concept_name: "客户",
            identity_scope: "customers",
            key_columns: ["id"],
            properties: [{ column: "id", name: "编号" }],
          },
        ],
      },
    },
  };
  let saved = structuredClone(run);
  let attempts = 0;
  await page.route("**/api/v1/alignment/runs", (route) =>
    route.fulfill({ json: [{ ...run, result: null }] }),
  );
  await page.route("**/api/v1/alignment/runs/template-original", (route) =>
    route.fulfill({ json: run }),
  );
  await page.route("**/api/v1/alignment/runs/template-saved", (route) =>
    route.fulfill({ json: saved }),
  );
  await page.route(
    "**/api/v1/alignment/runs/template-original/graph",
    async (route) => {
      attempts += 1;
      if (attempts === 1) {
        await route.fulfill({
          status: 409,
          json: { detail: "已有任务，请稍后重试" },
        });
        return;
      }
      saved = {
        ...run,
        id: "template-saved",
        result: {
          ...run.result,
          template: { ...route.request().postDataJSON(), confirmed: true },
        },
      };
      await route.fulfill({ status: 202, json: saved });
    },
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "02 数据转换" }).click();
  const generate = page.getByRole("button", {
    name: "采纳方案并生成",
    exact: true,
  });
  await expect(generate).toBeEnabled();
  await page
    .getByText("高级配置：实体拆分、跨表合并与关系", { exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "节点 1 身份范围", exact: true })
    .fill("shared-customers");
  await expect(generate).toBeEnabled();
  await generate.click();
  const dialog = page.getByRole("dialog", { name: "采纳匹配方案并生成图谱" });
  await dialog
    .getByRole("button", { name: "采纳并开始生成", exact: true })
    .click();
  await expect(dialog).toContainText("已有任务，请稍后重试");
  await expect(
    page.getByRole("textbox", { name: "节点 1 身份范围", exact: true }),
  ).toHaveValue("shared-customers");
  await dialog
    .getByRole("button", { name: "采纳并开始生成", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  expect(saved.result.template.nodes[0].identity_scope).toBe(
    "shared-customers",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/template-mobile.png",
    fullPage: true,
  });
});

test("failed intake can be retried and cancelled without losing task visibility", async ({
  page,
}) => {
  const job = {
    id: "upload-test",
    created_at: "2026-09-19T00:00:00Z",
    status: "failed",
    name: "流水.csv",
    size_bytes: 200000000,
    rows_done: 123456,
    attempt: 1,
    batch_id: null,
    error: "解析中断" as string | null,
  };
  await page.route("**/api/v1/intake/jobs", (route) =>
    route.fulfill({ json: [job] }),
  );
  await page.route("**/api/v1/intake/jobs/upload-test/retry", async (route) => {
    job.status = "queued";
    job.rows_done = 0;
    job.error = null;
    await route.fulfill({ json: job });
  });
  await page.route(
    "**/api/v1/intake/jobs/upload-test/cancel",
    async (route) => {
      job.status = "cancelled";
      await route.fulfill({ json: job });
    },
  );
  await page.goto("/");
  await expect(page.locator(".intake-job-row")).toContainText("123,456");
  await page.getByRole("button", { name: "重新处理", exact: true }).click();
  await expect(page.locator(".intake-job-row")).toContainText("等待处理");
  await page
    .locator(".intake-job-row")
    .getByRole("button", { name: "取消", exact: true })
    .click();
  await expect(page.locator(".intake-job-row")).toContainText("已取消");
  await expect(
    page.getByRole("button", { name: "重新处理", exact: true }),
  ).toBeVisible();
});
