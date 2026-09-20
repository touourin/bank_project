import { test, expect } from "@playwright/test";

test("staged data becomes a mapping, graph publication is separate, and reload retains results", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator(".upload-zone input[type=file]").setInputFiles({
    name: "对齐客户.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "id,name,amount\n0001,合成甲公司,12345678901234567890.1200\n0002,合成乙公司,",
    ),
  });
  await page.getByRole("button", { name: "解析并暂存（1 个文件）" }).click();
  await expect(
    page.getByRole("heading", { name: "对齐客户.csv", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "02 本体对齐与图谱生成" }).click();
  await expect(
    page.getByRole("heading", { name: "本体对齐与图谱生成", exact: true }),
  ).toBeVisible();
  await page
    .locator(".alignment-batch-name")
    .filter({ hasText: "对齐客户.csv" })
    .click();
  await page.locator(".alignment-tables").getByRole("checkbox").check();
  await page.getByRole("button", { name: "分析所选 1 张表" }).click();
  await expect(
    page.getByRole("heading", { name: "匹配结果", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("可生成实例", { exact: true })).toBeVisible();
  await expect(
    page.getByText("retrieve 接口匹配＋本地版本校验", { exact: false }).last(),
  ).toBeVisible();
  await expect(page.getByText("尚未生成图谱", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "匹配过程", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "理解表与字段含义", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".selected-concept")).toContainText("customer");
  const retrieval = page.getByRole("region", {
    name: "retrieve 匹配记录",
    exact: true,
  });
  await expect(retrieval).toContainText("customer");
  await expect(retrieval).toContainText("0.930");
  await expect(retrieval).toContainText("精确匹配");
  await page.getByRole("tab", { name: "字段映射", exact: true }).click();
  await expect(page.getByRole("region", { name: /字段映射/ })).toBeVisible();
  await page.getByRole("button", { name: "生成图谱", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "生成图谱版本" });
  await expect(dialog).toContainText("共 2 行");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByText("尚未生成图谱", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "生成图谱", exact: true }).click();
  await dialog.getByRole("button", { name: "开始生成", exact: true }).click();
  await expect(page.locator(".graph-metrics")).toContainText("2 个实例");
  await page
    .getByRole("button", { name: "查看 合成甲公司", exact: true })
    .click();
  await expect(page.getByRole("dialog", { name: "合成甲公司" })).toContainText(
    "0001",
  );
  await expect(page.getByRole("dialog", { name: "合成甲公司" })).toContainText(
    "12345678901234567890.1200",
  );
  await page.keyboard.press("Escape");
  // Revisions must preserve the published graph and the original model suggestion.
  await page.getByRole("button", { name: "修改表节点", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "修改表的本体节点" });
  await expect(editor).toContainText("本次检索候选，共 1 个");
  await editor
    .getByRole("searchbox", { name: "查找本体节点", exact: true })
    .fill("account");
  await editor.getByRole("button", { name: "查找", exact: true }).click();
  await editor
    .getByRole("button", { name: "选择 账户 account", exact: true })
    .click();
  await editor
    .getByRole("textbox", { name: "修改依据", exact: true })
    .fill("测试人工调整为账户");
  await editor
    .getByRole("button", { name: "保存为新版本", exact: true })
    .click();
  await expect(editor).not.toBeVisible();
  await expect(page.locator(".mapping-title")).toContainText("account");
  await expect(page.locator(".mapping-title")).toContainText("人工确认");
  await page.getByRole("tab", { name: "匹配过程", exact: true }).click();
  await expect(page.locator(".selected-concept")).toContainText("customer");
  await expect(page.locator(".graph-metrics")).toContainText("2 个实例");
  await expect(
    page.getByRole("button", { name: "生成图谱", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("tab", { name: "人工修改记录（1）", exact: true })
    .click();
  await expect(page.locator(".manual-edits")).toContainText(
    "测试人工调整为账户",
  );
  await page.getByRole("tab", { name: "字段映射", exact: true }).click();
  await page
    .getByRole("button", { name: "修改 name 的节点", exact: true })
    .click();
  const fieldEditor = page.getByRole("dialog", { name: "修改字段节点：name" });
  await fieldEditor
    .getByRole("button", { name: "选择 客户 customer", exact: true })
    .click();
  await fieldEditor
    .getByRole("textbox", { name: "修改依据", exact: true })
    .fill("测试字段映射调整");
  await fieldEditor
    .getByRole("button", { name: "保存为新版本", exact: true })
    .click();
  await expect(fieldEditor).not.toBeVisible();
  await expect(
    page.getByRole("tab", { name: "人工修改记录（2）", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/alignment-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/alignment-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.reload();
  await page.getByRole("tab", { name: "02 本体对齐与图谱生成" }).click();
  await expect(
    page.getByRole("heading", { name: "匹配结果", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".graph-metrics")).toContainText("2 个实例");
  await expect(page.locator(".mapping-title")).toContainText("account");
  await expect(
    page.getByRole("tab", { name: "人工修改记录（2）", exact: true }),
  ).toBeVisible();
});

test("low confidence remains visible and cannot generate a graph", async ({
  page,
}) => {
  const run = {
    id: "review-run",
    created_at: "2026-09-19T00:00:00Z",
    status: "ready",
    progress: "分析完成",
    error: null,
    graph_status: "none",
    graph_error: null,
    graph_version: null,
    result: {
      revision: "r1",
      snapshot_sha256: "sample",
      warnings: [],
      relations: [],
      tables: [
        {
          table_id: "t1",
          batch_id: "b1",
          table_name: "待核对表",
          row_count: 2,
          concept_id: "customer",
          concept_name: "客户",
          confidence: 0.4,
          status: "review",
          verification: "local_catalog",
          reason: "含义不明确",
          columns: [],
          warnings: ["置信度低于 75%，本次不生成实例"],
        },
      ],
    },
  };
  await page.route("**/api/v1/alignment/runs", (route) =>
    route.fulfill({ json: [{ ...run, result: null }] }),
  );
  await page.route("**/api/v1/alignment/runs/review-run", (route) =>
    route.fulfill({ json: run }),
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "02 本体对齐与图谱生成" }).click();
  await expect(page.getByText("需要核对", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "生成图谱", exact: true }),
  ).toBeDisabled();
  await page.getByRole("tab", { name: "匹配过程", exact: true }).click();
  await expect(page.getByText(/这条历史任务没有保存匹配过程/)).toBeVisible();
  await page.getByRole("tab", { name: "字段映射", exact: true }).click();
  await expect(
    page.getByText("原分析提示：置信度低于 75%，本次不生成实例", {
      exact: true,
    }),
  ).toBeVisible();
});

test("live stages are visible and interrupted jobs cannot pretend to finish", async ({
  page,
}) => {
  const run = {
    id: "live-run",
    created_at: "2026-09-19T00:00:00Z",
    status: "analyzing",
    progress: "正在选择节点",
    error: null as string | null,
    graph_status: "none",
    graph_error: null,
    graph_version: null,
    result: {
      revision: "r1",
      snapshot_sha256: "sample",
      warnings: [],
      relations: [],
      tables: [
        {
          table_id: "t1",
          batch_id: "b1",
          table_name: "分析中客户表",
          row_count: 100,
          concept_id: null,
          concept_name: null,
          confidence: 0,
          status: "unmatched",
          verification: "none",
          reason: "",
          columns: [],
          warnings: [],
          trace: {
            steps: [
              { key: "meaning", status: "completed", detail: "已理解表含义" },
              { key: "recall", status: "completed", detail: "召回 1 个表概念" },
              {
                key: "selection",
                status: "running",
                detail: "模型正在选择节点",
              },
              { key: "validation", status: "pending", detail: "" },
            ],
            sample_rows: [2, 26, 51, 76, 101],
            meaning: {
              meaning: "每行代表一个客户",
              search_terms: ["客户"],
              attribute_terms: [],
            },
            candidates: [
              {
                id: "customer",
                name: "客户",
                parents: [{ id: "entity", name: "实体" }],
              },
            ],
            attribute_candidates: [],
            selected: null,
            selection_reason: "",
            selection_confidence: null,
            verification: "none",
            selection_attempts: 1,
            confidence_threshold: 0.75,
          },
        },
      ],
    },
  };
  await page.route("**/api/v1/alignment/runs", (route) =>
    route.fulfill({ json: [{ ...run, result: null }] }),
  );
  await page.route("**/api/v1/alignment/runs/live-run", (route) =>
    route.fulfill({ json: run }),
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "02 本体对齐与图谱生成" }).click();
  await expect(
    page.getByText("每行代表一个客户", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".match-stage-running")).toContainText(
    "模型选择节点",
  );
  await expect(
    page.getByRole("button", { name: "修改表节点", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "生成图谱", exact: true }),
  ).not.toBeVisible();
  await page
    .getByText("查看候选节点（表概念 1 / 属性 0）", { exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "本次候选节点", exact: true }),
  ).toContainText("直属上级：实体（entity）");
  run.status = "failed";
  run.error = "任务已中断";
  await expect(page.locator(".match-stage-running")).toHaveCount(0);
  await expect(page.locator(".match-stage-failed")).toHaveCount(2);
  await expect(
    page.getByRole("button", { name: "修改表节点", exact: true }),
  ).toBeDisabled();
});

test("retrieve scores and low-confidence candidates remain reviewable", async ({
  page,
}) => {
  const candidate = { id: "customer", name: "客户", parents: [], score: 0.327 };
  const match = {
    target: "table",
    name: "接口验收表",
    query: "对公客户",
    status: "review",
    candidates: [candidate],
    selected: candidate,
    confident: true,
    match_method: "vector",
    detail: "得分不足，请人工确认",
  };
  const run = {
    id: "retrieve-review",
    created_at: "2026-09-20T00:00:00Z",
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
          table_name: "接口验收表",
          row_count: 2,
          concept_id: "customer",
          concept_name: "客户",
          confidence: 0.327,
          status: "review",
          verification: "verified",
          reason: "得分不足",
          columns: [],
          warnings: [],
          trace: {
            method: "retrieve",
            retrievals: [match],
            sample_rows: [2, 3],
            meaning: {
              meaning: "客户资料",
              search_terms: ["对公客户"],
              attribute_terms: [],
            },
            candidates: [candidate],
            attribute_candidates: [],
            selected: candidate,
            selection_reason: "得分不足",
            selection_confidence: 0.327,
            verification: "verified",
            selection_attempts: 0,
            confidence_threshold: 0.75,
            steps: ["meaning", "recall", "selection", "validation"].map(
              (key) => ({ key, status: "completed", detail: "已完成" }),
            ),
          },
        },
      ],
    },
  };
  await page.route("**/api/v1/alignment/runs", (route) =>
    route.fulfill({ json: [{ ...run, result: null }] }),
  );
  await page.route("**/api/v1/alignment/runs/retrieve-review", (route) =>
    route.fulfill({ json: run }),
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "02 本体对齐与图谱生成" }).click();
  await page.getByRole("tab", { name: "匹配过程", exact: true }).click();
  const records = page.getByRole("region", {
    name: "retrieve 匹配记录",
    exact: true,
  });
  await expect(records).toContainText("0.327");
  await expect(records).toContainText("向量匹配");
  await expect(records).toContainText("待确认");
  await expect(
    page.getByRole("button", { name: "生成图谱", exact: true }),
  ).toBeDisabled();
  await records.locator(".ant-table-row-expand-icon").first().click();
  await expect(records).toContainText("接口标记：有把握");
  await page.getByRole("button", { name: "修改表节点", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "修改表的本体节点" });
  await expect(editor).toContainText("本次检索候选，共 1 个");
  await expect(editor).toContainText("0.327");
  await editor
    .getByRole("button", { name: "选择 客户 customer", exact: true })
    .click();
  await expect(editor.getByText("将匹配到", { exact: true })).toBeVisible();
});
