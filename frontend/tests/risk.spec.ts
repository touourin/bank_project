import { expect, test, type Page } from "@playwright/test";
import type {
  PropagationJob,
  RiskCase,
  RiskExecution,
  RiskPredicate,
} from "../src/features/risk/types";

const graphVersion = "11111111-1111-4111-8111-111111111111";
const secondGraph = "22222222-2222-4222-8222-222222222222";
const predicate: RiskPredicate = {
  predicate: "cash_aggregate_threshold",
  name: "现金累计金额阈值",
  description: "按账户和自然日累计现金交易。",
  required_params: ["threshold", "n_min", "cash_scope", "status_filter"],
  optional_params: [],
  required_fields: [
    "account_id",
    "occurred_at",
    "amount",
    "status",
    "txn_type",
  ],
  optional_fields: [],
  parameter_schema: {},
};

function candidate(): RiskCase {
  return {
    id: "case-1",
    name: "现金存入累计风险",
    description: "核验现金存入累计模式",
    version: 1,
    content_hash: "a".repeat(64),
    review_status: "pending_review",
    execution_status: "blocked",
    rule_pack: {
      head: { risk_label: "现金累计风险", semantics: "pattern_not_intent" },
      body: [
        {
          predicate: predicate.predicate,
          params: {
            threshold: { value: 10000000, unit: "CNY_MINOR" },
            n_min: 2,
            cash_scope: "现金存入",
            status_filter: "成功",
            bo_scope: ["BO-CASH", "BO-LARGE"],
          },
        },
      ],
    },
    source_binding: {
      dataset_revision: "ontology-2026-09",
      snapshot_sha256: "b".repeat(64),
      anchor_node_id: "BO-CASH",
      anchor_node_name: "现金存入",
      source_node_id: "BFO-LIMIT",
      source_node_name: "现金存入限额",
      dimension_hash: "c".repeat(64),
      why: [
        {
          content:
            "同一账户同日成功现金存入至少两笔，累计超过十万元且单笔不超过十万元时提示风险。",
        },
      ],
      parameter_sources: { threshold: { quote: "累计超过十万元" } },
      propagation_path: [
        {
          relation: "inheres_in",
          from_id: "BFO-LIMIT",
          to_id: "BO-CASH",
          strength: "strong",
        },
      ],
      propagation: {
        bo_scope: ["BO-CASH", "BO-LARGE"],
        scope_hash: "d".repeat(64),
        path_strength: "strong",
      },
    },
    validation_issues: [],
    audits: [],
  };
}

async function mockRisk(
  page: Page,
  options: {
    initial?: boolean;
    noWhy?: boolean;
    invalid?: boolean;
    reviewConflict?: boolean;
    pollFailsOnce?: boolean;
  } = {},
) {
  let riskCase = options.initial === false ? undefined : candidate();
  if (riskCase && options.invalid)
    riskCase.validation_issues = [
      {
        field: "threshold",
        reason: "underdetermined",
        message: "WHY 未明确现金阈值",
      },
    ];
  let job: PropagationJob | undefined;
  let polls = 0;
  let failed = false;
  const requests: { path: string; body: Record<string, unknown> }[] = [];
  const searches: string[] = [];
  const executions: RiskExecution[] = [];
  await page.route(/\/api\/v1\/risk(?:\/|$)/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1/risk", "");
    const body =
      request.method() === "POST"
        ? (request.postDataJSON() as Record<string, unknown>)
        : {};
    if (request.method() === "POST") requests.push({ path, body });
    const send = (value: unknown, status = 200) =>
      route.fulfill({ status, json: value });
    if (path === "/catalog") {
      searches.push(url.searchParams.get("q") ?? "");
      return send({
        revision: "ontology-2026-09",
        snapshot_sha256: "b".repeat(64),
        nodes: [
          { id: "BO-CASH", name: "现金存入", has_why: false },
          { id: "BO-LARGE", name: "大额现金存入", has_why: false },
        ],
        predicates: [predicate],
      });
    }
    if (path === "/propagations" && request.method() === "POST") {
      job = {
        id: "job-1",
        status: "running",
        progress: "正在映射 WHY 来源",
        case_ids: [],
        coverage: { complete: false },
        dataset_revision: "ontology-2026-09",
        snapshot_sha256: "b".repeat(64),
        created_at: "2026-09-21T10:00:00+08:00",
        anchors: [],
        rejections: [],
      };
      return send(job);
    }
    if (path === "/propagations")
      return send(job ? [{ ...job, anchors: undefined }] : []);
    if (path === "/propagations/job-1" && job) {
      if (options.pollFailsOnce && !failed) {
        failed = true;
        return route.abort();
      }
      polls++;
      if (polls > 1) {
        if (!options.noWhy) riskCase = candidate();
        job = {
          ...job,
          status: "succeeded",
          progress: "WHY 映射已完成",
          case_ids: riskCase ? [riskCase.id] : [],
          coverage: { complete: true },
          anchors: [
            { anchor_node_id: "BO-CASH", propagation: { candidates: [] } },
          ],
          rejections: options.noWhy
            ? [{ anchor_node_id: "BO-CASH", reason: "没有可传导的 WHY 来源" }]
            : [],
        };
      }
      return send(job);
    }
    if (path === "/cases") return send(riskCase ? [riskCase] : []);
    if (path === "/cases/case-1") return send(riskCase);
    if (path === "/cases/case-1/review" && riskCase) {
      if (options.reviewConflict) {
        riskCase = {
          ...riskCase,
          version: 2,
          review_status: "rejected",
          execution_status: "blocked",
        };
        return send({ detail: "规则或审核状态已变化，请刷新后重新确认" }, 409);
      }
      riskCase = {
        ...riskCase,
        version: riskCase.version + 1,
        review_status: body.action === "approve" ? "approved" : "rejected",
        execution_status: body.action === "approve" ? "ready" : "blocked",
        audits: [
          {
            action: body.action,
            evidence_confirmed: body.evidence_confirmed,
            reason: body.reason,
          },
        ],
      };
      return send(riskCase);
    }
    if (path === "/sources")
      return send([
        { kind: "database", id: graphVersion, name: "现金交易图谱" },
        { kind: "database", id: secondGraph, name: "另一已发布图谱" },
      ]);
    if (path === "/cases/case-1/fields")
      return send({
        graph_version: url.searchParams.get("graph_version"),
        bo_scope: ["BO-CASH", "BO-LARGE"],
        sampled_count: 2,
        total_count: 2,
        truncated: false,
        fields: [
          { name: "客户账号", samples: ["00001"] },
          { name: "交易日期", samples: ["2026-09-01T09:00:00+08:00"] },
          { name: "交易金额", samples: ["80000.00"] },
          { name: "处理状态", samples: ["成功"] },
          { name: "业务类型", samples: ["现金存入"] },
        ].map((item) => ({ ...item, present_count: 2 })),
      });
    if (path === "/cases/case-1/executions" && request.method() === "POST") {
      const execution: RiskExecution = {
        id: "execution-1",
        status: "succeeded",
        created_at: "2026-09-21T11:00:00+08:00",
        graph_version: body.graph_version as string,
        start: body.start as string,
        end: body.end as string,
        field_mapping: body.field_mapping as Record<string, string>,
        result: {
          predicate: predicate.predicate,
          graph_version: body.graph_version as string,
          start: body.start as string,
          end: body.end as string,
          bo_scope: ["BO-CASH", "BO-LARGE"],
          counts: {
            scanned: 2,
            within_window: 2,
            matched_transactions: 2,
            hit_count: 1,
          },
          hits: [
            {
              id: "hit-1",
              account_id: "00001",
              day: "2026-09-01",
              count: 2,
              total: { value: 15000000, unit: "CNY_MINOR" },
              transactions: [
                {
                  id: "txn-1",
                  concept_id: "BO-CASH",
                  fields: { amount: { value: 8000000, unit: "CNY_MINOR" } },
                },
              ],
            },
          ],
          truncation: { scope: false, hits: false, evidence: true },
          limits: { scope: 50000, hit: 200, evidence_per_hit: 20 },
        },
      };
      executions.unshift(execution);
      return send(execution);
    }
    if (path === "/cases/case-1/executions") return send(executions);
    return send({ detail: `Unexpected fixture request ${path}` }, 404);
  });
  await page.goto("/");
  await page.getByRole("tab", { name: "05 风险规则" }).click();
  return { requests, searches };
}

async function select(page: Page, label: string, option: string) {
  const combobox = page.getByRole("combobox", { name: label, exact: true });
  await combobox.click();
  const controls = await combobox.getAttribute("aria-controls");
  await page
    .locator(".ant-select-dropdown")
    .filter({ has: page.locator(`[id="${controls}"]`) })
    .locator(".ant-select-item-option")
    .filter({ hasText: option })
    .click();
}

test("WHY generation is polled, recovers read failure and binds sources for review", async ({
  page,
}) => {
  const { requests, searches } = await mockRisk(page, {
    initial: false,
    pollFailsOnce: true,
  });
  await expect(
    page.getByRole("button", { name: "生成待审核规则" }),
  ).toBeDisabled();
  await page.getByRole("textbox", { name: "搜索 BO 节点" }).fill("现金");
  await expect.poll(() => searches).toContain("现金");
  await select(page, "选择风险锚点", "现金存入 · BO-CASH");
  await page
    .getByRole("textbox", { name: "风险分析需求" })
    .fill("核验现金累计金额模式");
  await page.getByRole("button", { name: "生成待审核规则" }).click();
  await expect(
    page.getByText("正在映射 WHY 来源", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "生成待审核规则" }),
  ).toBeDisabled();
  await expect(
    page.getByText("连接中断。请刷新结果确认操作状态，再决定是否重试。"),
  ).toBeVisible();
  await page.getByRole("button", { name: "恢复查询状态" }).click();
  await expect(page.getByText("已生成 1 条待审核规则")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "现金存入累计风险", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("现金存入限额 · BFO-LIMIT", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".risk-path")).toContainText("inheres_in");
  await expect(page.locator(".risk-scope")).toHaveText("BO-CASHBO-LARGE");
  await expect(page.locator(".risk-scope")).not.toContainText("BFO-LIMIT");
  expect(requests.filter((item) => item.path === "/propagations")).toEqual([
    {
      path: "/propagations",
      body: {
        anchor_node_ids: ["BO-CASH"],
        brief: "核验现金累计金额模式",
        dataset_revision: "ontology-2026-09",
        max_depth: 5,
        max_candidates: 50,
      },
    },
  ]);
});

test("a completed task without WHY does not claim any generated rule", async ({
  page,
}) => {
  await mockRisk(page, { initial: false, noWhy: true });
  await select(page, "选择风险锚点", "现金存入 · BO-CASH");
  await page
    .getByRole("textbox", { name: "风险分析需求" })
    .fill("核验现金风险");
  await page.getByRole("button", { name: "生成待审核规则" }).click();
  await expect(page.getByText("未生成规则", { exact: true })).toBeVisible();
  await expect(page.getByText("暂无风险规则", { exact: true })).toBeVisible();
  await page.getByText("固定快照、覆盖与筛除记录", { exact: true }).click();
  await expect(page.getByText(/没有可传导的 WHY 来源/)).toBeVisible();
  await expect(page.getByRole("button", { name: "批准规则" })).toHaveCount(0);
  await page.reload();
  await page.getByRole("tab", { name: "05 风险规则" }).click();
  await page.getByText("固定快照、覆盖与筛除记录", { exact: true }).click();
  await expect(page.locator(".risk-details pre")).toContainText(
    '"candidates": []',
  );
});

test("approval requires evidence and execution requires explicit fields and zoned dates", async ({
  page,
}) => {
  const { requests } = await mockRisk(page);
  await expect(page.getByRole("button", { name: "批准规则" })).toBeDisabled();
  await expect(
    page.getByText("审核通过后开放执行", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "执行已批准规则" }),
  ).toHaveCount(0);
  await page
    .getByRole("checkbox", {
      name: "我已核对 WHY 原文、参数引用、传导适用性及实例作用域",
    })
    .check();
  await page
    .getByRole("textbox", { name: "风险规则审核意见" })
    .fill("已核验限额及类继承范围");
  await page.getByRole("button", { name: "批准规则" }).click();
  await expect(
    page.getByRole("button", { name: "执行已批准规则" }),
  ).toBeDisabled();
  expect(requests.find((item) => item.path.endsWith("/review"))?.body).toEqual({
    expected_version: 1,
    expected_hash: "a".repeat(64),
    action: "approve",
    evidence_confirmed: true,
    reason: "已核验限额及类继承范围",
  });
  await select(page, "风险执行图谱", "现金交易图谱");
  await select(page, "映射 account_id", "客户账号");
  await select(page, "风险执行图谱", "另一已发布图谱");
  await expect(
    page.getByRole("combobox", { name: "映射 account_id" }),
  ).toHaveValue("");
  await select(page, "风险执行图谱", "现金交易图谱");
  for (const [canonical, original] of Object.entries({
    account_id: "客户账号",
    occurred_at: "交易日期",
    amount: "交易金额",
    status: "处理状态",
    txn_type: "业务类型",
  }))
    await select(page, `映射 ${canonical}`, original);
  await page
    .getByRole("textbox", { name: "风险开始时间" })
    .fill("2026-09-01T00:00:00");
  await page
    .getByRole("textbox", { name: "风险结束时间" })
    .fill("2026-09-02T00:00:00");
  await expect(
    page.getByRole("button", { name: "执行已批准规则" }),
  ).toBeDisabled();
  await expect(page.getByText("请填写有效的带时区时间范围")).toBeVisible();
  await page
    .getByRole("textbox", { name: "风险开始时间" })
    .fill("2026-09-01T00:00:00+08:00");
  await page
    .getByRole("textbox", { name: "风险结束时间" })
    .fill("2026-09-02T00:00:00+08:00");
  await page.getByRole("button", { name: "执行已批准规则" }).click();
  await expect(
    page.getByRole("region", { name: "风险命中结果" }),
  ).toContainText("00001");
  await expect(
    page.getByRole("region", { name: "风险命中结果" }),
  ).toContainText("15000000 分（人民币）");
  await expect(page.getByText("展示结果已达到上限")).toBeVisible();
  expect(
    requests.find((item) => item.path.endsWith("/executions"))?.body,
  ).toEqual({
    expected_version: 2,
    expected_hash: "a".repeat(64),
    graph_version: graphVersion,
    field_mapping: {
      account_id: "客户账号",
      occurred_at: "交易日期",
      amount: "交易金额",
      status: "处理状态",
      txn_type: "业务类型",
    },
    start: "2026-09-01T00:00:00+08:00",
    end: "2026-09-02T00:00:00+08:00",
  });
  await page.screenshot({
    path: "test-results/risk-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/risk-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
});

test("missing WHY parameters block approval and allow a reasoned rejection", async ({
  page,
}) => {
  const { requests } = await mockRisk(page, { invalid: true });
  await expect(
    page.getByRole("region", { name: "规则校验问题" }),
  ).toContainText("WHY 未明确现金阈值");
  await expect(
    page.getByRole("checkbox", {
      name: "我已核对 WHY 原文、参数引用、传导适用性及实例作用域",
    }),
  ).toBeDisabled();
  await expect(page.getByRole("button", { name: "批准规则" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "驳回规则" })).toBeDisabled();
  await page
    .getByRole("textbox", { name: "风险规则审核意见" })
    .fill("缺少阈值原文依据，需补充 WHY");
  await page.getByRole("button", { name: "驳回规则" }).click();
  await expect(page.getByRole("button", { name: "驳回规则" })).toBeDisabled();
  expect(requests[0].body).toMatchObject({
    action: "reject",
    evidence_confirmed: false,
    reason: "缺少阈值原文依据，需补充 WHY",
  });
  await expect(
    page.getByRole("button", { name: "执行已批准规则" }),
  ).toHaveCount(0);
});

test("returning to risk refreshes published graphs without losing execution inputs", async ({
  page,
}) => {
  await mockRisk(page);
  let available = [
    { kind: "database", id: graphVersion, name: "现金交易图谱" },
  ];
  await page.route("**/api/v1/risk/sources", (route) =>
    route.fulfill({ json: available }),
  );
  await page
    .getByRole("checkbox", {
      name: "我已核对 WHY 原文、参数引用、传导适用性及实例作用域",
    })
    .check();
  await page.getByRole("button", { name: "批准规则" }).click();
  await select(page, "风险执行图谱", "现金交易图谱");
  await select(page, "映射 account_id", "客户账号");
  await page
    .getByRole("textbox", { name: "风险开始时间" })
    .fill("2026-09-01T00:00:00+08:00");
  await page.getByRole("tab", { name: "01 数据接入" }).click();
  available.push({ kind: "database", id: secondGraph, name: "刚发布的图谱" });
  await page.getByRole("tab", { name: "05 风险规则" }).click();
  await expect(page.getByRole("textbox", { name: "风险开始时间" })).toHaveValue(
    "2026-09-01T00:00:00+08:00",
  );
  await page.getByRole("combobox", { name: "风险执行图谱" }).click();
  await expect(
    page.locator(".ant-select-item-option").filter({ hasText: "刚发布的图谱" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.locator(".risk-field").filter({
      has: page.getByRole("combobox", {
        name: "映射 account_id",
        exact: true,
      }),
    }),
  ).toContainText("客户账号");
  available = [
    { kind: "database", id: graphVersion, name: "现金交易图谱（已刷新）" },
  ];
  await page.getByRole("button", { name: "刷新图谱列表" }).click();
  await expect(
    page.locator(".risk-field").filter({
      has: page.getByRole("combobox", { name: "风险执行图谱", exact: true }),
    }),
  ).toContainText("现金交易图谱（已刷新）");
});

test("review conflict refreshes the current status without replaying approval", async ({
  page,
}) => {
  const { requests } = await mockRisk(page, { reviewConflict: true });
  await page
    .getByRole("checkbox", {
      name: "我已核对 WHY 原文、参数引用、传导适用性及实例作用域",
    })
    .check();
  await page.getByRole("button", { name: "批准规则" }).click();
  await expect(
    page.getByText("规则或审核状态已变化，请刷新后重新确认"),
  ).toBeVisible();
  await expect(
    page.getByRole("checkbox", {
      name: "我已核对 WHY 原文、参数引用、传导适用性及实例作用域",
    }),
  ).not.toBeChecked();
  await expect(
    page.getByRole("button", { name: "执行已批准规则" }),
  ).toHaveCount(0);
  expect(requests.filter((item) => item.path.endsWith("/review"))).toHaveLength(
    1,
  );
});

test("risk step is disabled in demo mode", async ({ page }) => {
  await page.goto("/?demo=1");
  await expect(page.getByRole("tab", { name: "05 风险规则" })).toBeDisabled();
});

test("all five workflow tabs remain clickable and keyboard accessible on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockRisk(page);
  const risk = page.getByRole("tab", { name: "05 风险规则" });
  const analysis = page.getByRole("tab", { name: "04 图谱浏览与分析" });
  const resolution = page.getByRole("tab", { name: "03 实体消歧" });
  await expect(risk).toHaveAttribute("aria-selected", "true");
  await risk.press("Home");
  await expect(page.getByRole("tab", { name: "01 数据接入" })).toBeFocused();
  await page.keyboard.press("End");
  await expect(risk).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(analysis).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(resolution).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "实体消歧", exact: true }),
  ).toBeVisible();
  await risk.click();
  await expect(
    page.getByRole("heading", { name: "风险规则", exact: true }),
  ).toBeVisible();
  await resolution.click();
  await expect(
    page.getByRole("heading", { name: "实体消歧", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/workflow-tabs-mobile.png",
    animations: "disabled",
  });
});
