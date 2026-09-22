import { expect, test } from "@playwright/test";
import { exampleRun } from "./helpers/alignment";

for (const source of ["tables", "text"] as const) {
  test(`${source} conversion waits for a slow poll and stops at completion`, async ({
    page,
  }) => {
    await page.clock.install();
    const run = exampleRun();
    run.status = "analyzing";
    run.progress = "等待本次转换结果";
    const dataset = {
      key: "slow-text",
      name: "慢任务.txt",
      status: "indexing",
      stage: "等待本次转换结果",
      progress: 0.5,
    };
    let hold = false;
    let finished = false;
    let heldRequests = 0;
    let release!: () => void;
    const responseReady = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/v1/alignment/runs", (route) =>
      route.fulfill({ json: source === "tables" ? [run] : [] }),
    );
    await page.route("**/api/v1/graphrag/config", (route) =>
      route.fulfill({ json: { configured: true } }),
    );
    await page.route("**/api/v1/graphrag/datasets/slow-text/graph", (route) =>
      route.fulfill({
        json: {
          id: "slow-text",
          name: "慢任务.txt",
          source_kind: "graphrag",
          source_id: "slow-text",
          nodes: [],
          edges: [],
        },
      }),
    );
    const endpoint =
      source === "tables"
        ? `**/api/v1/alignment/runs/${run.id}`
        : "**/api/v1/graphrag/datasets";
    await page.route(endpoint, async (route) => {
      if (hold) {
        heldRequests += 1;
        await responseReady;
      }
      await route.fulfill({
        json:
          source === "tables"
            ? { ...run, status: finished ? "ready" : "analyzing" }
            : [
                {
                  ...dataset,
                  status: finished ? "succeeded" : "indexing",
                  stage: finished ? "转换已结束" : dataset.stage,
                },
              ],
      });
    });
    await page.goto("/");
    await page.getByRole("tab", { name: "02 数据转换" }).click();
    if (source === "text")
      await page.getByRole("tab", { name: "TXT 文本", exact: true }).click();
    await expect(
      page.getByText("等待本次转换结果", { exact: true }).first(),
    ).toBeVisible();
    hold = true;
    await page.clock.runFor(2600);
    await expect.poll(() => heldRequests).toBe(1);
    await page.clock.runFor(10000);
    expect(heldRequests).toBe(1);
    finished = true;
    release();
    if (source === "tables")
      await expect(page.locator(".run-progress")).toContainText("规则已就绪");
    else
      await expect(
        page.getByRole("button", { name: "文本转换已完成" }),
      ).toBeVisible();
    await page.clock.runFor(10000);
    expect(heldRequests).toBe(1);
  });
}

test("switching table tasks ignores a late response from the previous selection", async ({
  page,
}) => {
  const first = exampleRun();
  const second = structuredClone(first);
  second.id = "second-task";
  second.result!.tables[0].table_name = "另一个任务的数据";
  let hold = false;
  let pending = false;
  let release!: () => void;
  const responseReady = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/v1/alignment/runs", (route) =>
    route.fulfill({ json: [first, second] }),
  );
  await page.route(`**/api/v1/alignment/runs/${first.id}`, async (route) => {
    if (hold) {
      pending = true;
      await responseReady;
    }
    await route.fulfill({ json: first });
  });
  await page.route(`**/api/v1/alignment/runs/${second.id}`, (route) =>
    route.fulfill({ json: second }),
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "02 数据转换" }).click();
  await expect(page.locator(".task-sources")).toContainText("客户");
  hold = true;
  await page.getByRole("button", { name: "刷新任务", exact: true }).click();
  await expect.poll(() => pending).toBe(true);
  await page.getByRole("combobox", { name: "选择分析任务" }).click();
  await page
    .getByText(/ · second-t/)
    .last()
    .click();
  await expect(page.locator(".task-sources")).toContainText("另一个任务的数据");
  release();
  await expect(page.locator(".mapping-title")).toContainText(
    "另一个任务的数据",
  );
});
