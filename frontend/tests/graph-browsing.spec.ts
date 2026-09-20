import { expect, test, type Page } from "@playwright/test";
import { graphElements } from "../src/features/alignment/graphPresentation";
import type { GraphNodeBrief } from "../src/features/alignment/types";

const version = "a92c5148-cba9-4d1b-af89-cec9318e9840";
const nodes: GraphNodeBrief[] = Array.from({ length: 237 }, (_, i) => ({
  id: String(i).padStart(5, "0"),
  name: i % 2 === 0 ? `合成客户${i}` : `旅程事件${i}`,
  table_id: i % 2 === 0 ? "customers" : "events",
  table_name: "数据",
  source_row: i + 2,
  concept_id: i % 2 === 0 ? "customer" : "event",
  concept_name: i % 2 === 0 ? "对公客户" : "业务事件",
}));
const groups = [
  { concept_id: "customer", concept_name: "对公客户", count: 119 },
  { concept_id: "event", concept_name: "业务事件", count: 118 },
];
const edges = [
  {
    source: "00000",
    target: "00220",
    relation_id: "r1",
    name: "有依据的关联",
    origin: "confirmed",
  },
];

async function openGraph(page: Page, relations = false) {
  await page.route("**/api/v1/alignment/runs", (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route("**/api/v1/alignment/graph/overview", (route) =>
    route.fulfill({
      json: {
        summary: {
          version,
          run_id: "task",
          revision: "r1",
          node_count: 237,
          edge_count: relations ? 1 : 0,
          created_at: "2026-09-20T09:00:00Z",
        },
        groups,
      },
    }),
  );
  await page.route(`**/api/v1/alignment/graph/${version}/nodes?*`, (route) => {
    const p = new URL(route.request().url()).searchParams;
    const q = p.get("q") || "",
      concept = p.get("concept") || "",
      focus = p.get("focus") || "",
      after = p.get("after") || "",
      limit = Number(p.get("limit") || 100);
    const matching = nodes.filter(
      (n) =>
        (!concept || n.concept_id === concept) &&
        (!q || n.name.includes(q)) &&
        (!focus || (relations && n.id === "00220")),
    );
    const remaining = matching.filter((n) => n.id > after);
    const selected = remaining.slice(0, limit);
    const ids = new Set([...selected.map((n) => n.id), focus]);
    return route.fulfill({
      json: {
        nodes: selected,
        total: matching.length,
        next_cursor: remaining.length > limit ? selected.at(-1)!.id : null,
        anchor: nodes.find((n) => n.id === focus) || null,
        edges: relations
          ? edges.filter((e) => ids.has(e.source) && ids.has(e.target))
          : [],
        edges_truncated: false,
      },
    });
  });
  await page.route(`**/api/v1/alignment/graph/${version}/nodes/*`, (route) => {
    const node = nodes.find(
      (n) => n.id === new URL(route.request().url()).pathname.split("/").at(-1),
    )!;
    return route.fulfill({
      json: {
        ...node,
        fields: {
          姓名: node.name,
          编号: node.id,
          备注: "<script>literal</script>",
          空值: null,
          长数值: "12345678901234567890.1200",
        },
      },
    });
  });
  await page.goto("/");
  await page.getByRole("tab", { name: "02 本体对齐与图谱生成" }).click();
  await expect(page.locator(".graph-page-status")).toContainText(
    "第 1–100 条 / 共 237 条",
  );
  await expect(page.locator(".graph-network canvas").first()).toBeVisible();
}

test("classification edges are presentation only; business mode uses stored edges", () => {
  const overview = graphElements(
    nodes.slice(0, 3),
    groups,
    edges,
    "classification",
  );
  expect(overview.filter((e) => e.data.kind === "membership")).toHaveLength(3);
  expect(
    overview.filter((e) => e.data.kind === "type").map((e) => e.data.label),
  ).toContain("对公客户\n119 个实例");
  const business = graphElements(
    [nodes[0], nodes[220]],
    groups,
    edges,
    "business",
  );
  expect(business.filter((e) => e.data.kind === "membership")).toHaveLength(0);
  expect(business.filter((e) => e.data.kind === "business")).toHaveLength(1);
  expect(
    graphElements(nodes.slice(0, 3), groups, [], "business").filter(
      (e) => e.data.source,
    ),
  ).toHaveLength(0);
});

test("all records can be paged, globally searched and inspected without changing publication", async ({
  page,
}) => {
  const mutations: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/alignment/") && request.method() !== "GET")
      mutations.push(request.method());
  });
  await openGraph(page);
  await expect(page.locator(".graph-metrics")).toContainText("237 个实例");
  await expect(page.locator(".graph-metrics")).toContainText("0 条业务关联");
  await expect(page.locator(".graph-view-caption")).toContainText("不代表交易");
  await page.getByRole("button", { name: "下一批", exact: true }).click();
  await expect(page.locator(".graph-page-status")).toContainText(
    "第 101–200 条 / 共 237 条",
  );
  await page.getByRole("button", { name: "下一批", exact: true }).click();
  await expect(page.locator(".graph-page-status")).toContainText(
    "第 201–237 条 / 共 237 条",
  );
  await expect(
    page.getByRole("button", { name: "下一批", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "上一批", exact: true }).click();
  await expect(page.locator(".graph-page-status")).toContainText(
    "第 101–200 条",
  );
  await page
    .getByRole("searchbox", { name: "搜索全量图谱" })
    .fill("合成客户220");
  await page.getByRole("searchbox", { name: "搜索全量图谱" }).press("Enter");
  await expect(page.locator(".graph-page-status")).toContainText("共 1 条");
  await page
    .getByRole("button", { name: "查看 合成客户220", exact: true })
    .click();
  const detail = page.getByRole("dialog", { name: "合成客户220" });
  await expect(detail).toContainText("12345678901234567890.1200");
  await expect(detail).toContainText("<script>literal</script>");
  await expect(detail).toContainText("NULL");
  await detail.getByRole("button", { name: "查看此实例的业务关联" }).click();
  await expect(
    page.getByText("当前版本没有业务关系", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".graph-page-status")).toContainText(
    "没有匹配记录",
  );
  await page.getByRole("button", { name: "返回全部数据", exact: true }).click();
  await page.getByText("本体分类", { exact: true }).click();
  await page
    .getByRole("button", { name: "对公客户 · 119", exact: true })
    .click();
  await expect(page.locator(".graph-page-status")).toContainText("共 119 条");
  await page.getByRole("button", { name: "返回全部数据", exact: true }).click();
  await expect(page.locator(".graph-page-status")).toContainText("共 237 条");
  await page.getByRole("button", { name: "展开图谱", exact: true }).click();
  await expect(page.locator(".graph-network-expanded")).toBeVisible();
  await page.getByRole("button", { name: "放大图谱", exact: true }).click();
  await page.getByRole("button", { name: "适应画布", exact: true }).click();
  await page
    .locator(".graph-network")
    .screenshot({ path: "test-results/graph-network-light.png" });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "切换深浅主题" }).click();
  await page
    .locator(".graph-network")
    .screenshot({ path: "test-results/graph-network-dark.png" });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出图谱图片" }).click();
  expect((await download).suggestedFilename()).toContain("本体分类网络");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".graph-network canvas").first()).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() => ({
        width: document.documentElement.scrollWidth,
        viewport: window.innerWidth,
      })),
    )
    .toEqual({ width: 390, viewport: 390 });
  await page
    .locator(".graph-network")
    .screenshot({ path: "test-results/graph-network-mobile.png" });
  expect(mutations).toEqual([]);
});

test("neighborhood can display related instances outside the current batch", async ({
  page,
}) => {
  await openGraph(page, true);
  await page
    .getByRole("button", { name: "查看 合成客户0", exact: true })
    .click();
  await page.getByRole("button", { name: "查看此实例的业务关联" }).click();
  await expect(page.locator(".graph-scope")).toContainText(
    "合成客户0 的关联对象",
  );
  await expect(
    page.getByRole("button", { name: "查看 合成客户220", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".graph-network-heading")).toContainText(
    "业务关系网络",
  );
  await expect(page.locator(".graph-network-heading")).toContainText(
    "2 个已展开实例",
  );
});

test("failed browsing preserves overview and can retry without changing data", async ({
  page,
}) => {
  await openGraph(page);
  await page.route(
    `**/api/v1/alignment/graph/${version}/nodes?*`,
    (route) =>
      route.fulfill({ status: 503, json: { detail: "图谱读取暂时不可用" } }),
    { times: 1 },
  );
  await page.getByRole("button", { name: "下一批", exact: true }).click();
  await expect(
    page.getByText("图谱读取暂时不可用", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".graph-metrics")).toContainText("237 个实例");
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(page.locator(".graph-page-status")).toContainText(
    "第 101–200 条",
  );
});
