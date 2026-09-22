import { expect, test } from "@playwright/test";
import type { OntologyGraph } from "../src/features/ontology/api";

const ontology: OntologyGraph = {
  source: {
    kind: "remote",
    ontology_id: "ontology-1",
    revision: "r1",
    snapshot_sha256: "a".repeat(64),
    node_count: 2,
    relation_count: 1,
    why_node_count: 1,
  },
  graph: {
    id: "ontology:ontology-1:r1",
    name: "BFO 本体",
    nodes: [
      {
        id: "cash",
        name: "现金交易",
        type: "过程型BO",
        properties: { has_why: false },
      },
      {
        id: "limit",
        name: "现金限额",
        type: "属性型BO",
        properties: { has_why: true },
      },
    ],
    edges: [
      {
        id: "r1",
        source: "limit",
        target: "cash",
        edge_type: "INHERES_IN",
        properties: {},
      },
    ],
  },
};

test("BFO browsing shows remote types, verified dimensions and complete topology without writes", async ({
  page,
}) => {
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/") && request.method() !== "GET")
      writes.push(request.url());
  });
  await page.route("**/api/v1/ontology/graph", (route) =>
    route.fulfill({ json: ontology }),
  );
  await page.route("**/api/v1/ontology/concepts/*/dimensions?*", (route) => {
    const url = new URL(route.request().url());
    expect(url.searchParams.get("revision")).toBe("r1");
    expect(url.searchParams.get("snapshot_sha256")).toBe(
      ontology.source.snapshot_sha256,
    );
    return route.fulfill({
      json: { what: { 定义: "现金交易限额" }, why: { 依据: "原始业务依据" } },
    });
  });
  await page.goto("/");
  await page
    .getByRole("tab", { name: "04 图谱浏览与分析", exact: true })
    .click();
  await page.getByRole("tab", { name: "BFO 本体", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "BFO 本体浏览" }),
  ).toBeVisible();
  await expect(page.getByText("远端本体", { exact: true })).toBeVisible();
  await expect(page.getByLabel("节点类型图例")).toContainText("属性型BO");
  await expect(page.getByLabel("节点类型图例")).toContainText("过程型BO");
  await page.getByRole("switch", { name: "只看含 WHY 的概念" }).click();
  await page.getByRole("combobox", { name: "选择本体概念查看依据" }).click();
  await expect(page.getByRole("option", { name: /现金交易/ })).toHaveCount(0);
  await page
    .getByText("现金限额 · limit · 属性型BO · 有 WHY", { exact: true })
    .click();
  await page.getByRole("button", { name: "WHY · 业务依据" }).click();
  await expect(page.getByText(/原始业务依据/)).toBeVisible();
  await page.getByRole("button", { name: "WHAT · 概念定义" }).click();
  await expect(page.getByText(/现金交易限额/)).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出完整图谱" }).click();
  expect((await download).suggestedFilename()).toBe("BFO 本体-graph.json");
  await page.screenshot({
    path: "test-results/ontology-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBe(390);
  await page.screenshot({
    path: "test-results/ontology-mobile.png",
    fullPage: true,
  });
  await page.route("**/api/v1/ontology/graph", (route) =>
    route.fulfill({ status: 503, json: { detail: "远端本体暂时不可用" } }),
  );
  await page.getByRole("button", { name: "刷新本体", exact: true }).click();
  await expect(
    page.getByText("远端本体暂时不可用", { exact: true }),
  ).toBeVisible();
  expect(writes).toEqual([]);
});
