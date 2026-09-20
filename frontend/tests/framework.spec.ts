import { test, expect } from "@playwright/test";

test("first step connects, preserves theme and fits mobile", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "数据接入", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".topbar .status")).toHaveText("服务已连接");
  await expect(page.getByRole("tab", { name: "文件上传" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "MySQL 数据库" })).toBeVisible();
  await page.getByRole("button", { name: "切换深浅主题" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "切换深浅主题" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/intake-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
});

test("CSV upload preserves values, paginates and opens field structure", async ({
  page,
}) => {
  await page.goto("/");
  const rows = Array.from(
    { length: 28 },
    (_, i) =>
      `${String(i + 1).padStart(6, "0")},客户${i + 1},12345678901234567890.1200`,
  );
  await page.locator(".upload-zone input[type=file]").setInputFiles({
    name: "客户测试.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("客户编号,客户姓名,金额\n" + rows.join("\n")),
  });
  await page.getByRole("button", { name: "解析并暂存（1 个文件）" }).click();
  await expect(
    page.getByRole("heading", { name: "客户测试.csv", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "000001", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "12345678901234567890.1200", exact: true }),
  ).toHaveCount(25);
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "000026", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("radiogroup", { name: "预览方式" })
    .getByText("字段结构", { exact: true })
    .click();
  await expect(
    page.getByRole("cell", { name: "数据观察", exact: true }),
  ).toHaveCount(3);
  await page
    .getByRole("radiogroup", { name: "预览方式" })
    .getByText("数据预览", { exact: true })
    .click();
  await page.screenshot({
    path: "test-results/intake-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/intake-data-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.reload();
  await page.getByRole("button", { name: /客户测试.csv.*28 行/ }).click();
  await expect(
    page.getByRole("cell", { name: "000001", exact: true }),
  ).toBeVisible();
});

test("same-name files create independent batches and invalid upload keeps existing data", async ({
  page,
}) => {
  await page.goto("/");
  for (const value of ["001", "002"]) {
    await page.locator(".upload-zone input[type=file]").setInputFiles({
      name: "同名.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(`id,name\n${value},客户`),
    });
    await page.getByRole("button", { name: "解析并暂存（1 个文件）" }).click();
    await expect(
      page.getByRole("cell", { name: value, exact: true }),
    ).toBeVisible();
  }
  await expect(
    page.locator(".batch-row").filter({ hasText: "同名.csv" }),
  ).toHaveCount(2);
  await page.locator(".upload-zone input[type=file]").setInputFiles({
    name: "坏数据.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("id,id\n1,2"),
  });
  await page.getByRole("button", { name: "解析并暂存（1 个文件）" }).click();
  await expect(page.locator(".result-card.failed")).toContainText("重复列名");
  await expect(
    page.getByRole("cell", { name: "002", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "移除数据" }).click();
  const dialog = page.getByRole("dialog", { name: "移除数据" });
  await expect(dialog).toContainText("同名.csv");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(
    page.locator(".batch-row").filter({ hasText: "同名.csv" }),
  ).toHaveCount(2);
  await page.getByRole("button", { name: "移除数据" }).click();
  await page.route("**/api/v1/intake/batches/*", async (route) => {
    if (route.request().method() === "DELETE")
      await route.fulfill({
        status: 503,
        json: { detail: "暂时无法删除，请重试" },
      });
    else await route.fallback();
  });
  await dialog.getByRole("button", { name: "确认移除", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("暂时无法删除");
  await expect(
    page.locator(".batch-row").filter({ hasText: "同名.csv" }),
  ).toHaveCount(2);
  await page.unroute("**/api/v1/intake/batches/*");
  await dialog.getByRole("button", { name: "确认移除", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(
    page.locator(".batch-row").filter({ hasText: "同名.csv" }),
  ).toHaveCount(1);
});

test("MySQL table selection resets when connection changes and password is not persisted", async ({
  page,
}) => {
  await page.route("**/api/v1/intake/mysql/catalog", (route) =>
    route.fulfill({
      json: [{ name: "customers", comment: "客户", estimated_rows: 10 }],
    }),
  );
  await page.goto("/");
  await page.getByRole("tab", { name: "MySQL 数据库" }).click();
  await page.getByLabel("使用项目配置的 MySQL").uncheck();
  await page.getByLabel("数据库地址").fill("mysql");
  await page.getByLabel("数据库名", { exact: true }).fill("bank");
  await page.getByLabel("用户名", { exact: true }).fill("reader");
  await page.getByLabel("密码", { exact: true }).fill("browser-secret");
  await page.getByRole("button", { name: "连接并列出数据表" }).click();
  await page.getByRole("checkbox", { name: /customers/ }).check();
  await expect(
    page.getByRole("button", { name: "读取所选表并暂存" }),
  ).toBeEnabled();
  await page.getByLabel("数据库名", { exact: true }).fill("other");
  await expect(page.locator(".catalog-list input")).toHaveCount(0);
  const saved = await page.evaluate(() =>
    JSON.stringify({ ...localStorage, ...sessionStorage }),
  );
  expect(saved).not.toContain("browser-secret");
});

test("health connection failure is visible and can recover", async ({
  page,
}) => {
  await page.route("**/health", (route) => route.abort());
  await page.goto("/");
  await expect(page.locator(".health-error")).toContainText("无法连接后端服务");
  await page.unroute("**/health");
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await expect(page.locator(".topbar .status")).toHaveText("服务已连接");
  await expect(page.locator(".health-error")).toHaveCount(0);
});

test("multiple files wait for confirmation, survive tab switches and report partial success", async ({
  page,
}) => {
  let requests = 0;
  page.on("request", (request) => {
    if (request.url().includes("/intake/uploads")) requests++;
  });
  await page.goto("/");
  await page.locator(".upload-zone input[type=file]").setInputFiles([
    {
      name: "批量坏数据.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("id,id\n1,2"),
    },
    {
      name: "批量正常.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("id,name\n009,测试客户"),
    },
  ]);
  await expect(
    page.getByRole("button", { name: "解析并暂存（2 个文件）" }),
  ).toBeEnabled();
  await page.getByRole("tab", { name: "MySQL 数据库" }).click();
  await page.getByRole("tab", { name: "文件上传" }).click();
  expect(requests).toBe(0);
  await page.getByRole("button", { name: "解析并暂存（2 个文件）" }).click();
  await expect(page.locator(".result-card.failed")).toContainText("重复列名");
  await expect(
    page.locator(".result-card").filter({ hasText: "批量正常.csv" }),
  ).toContainText("已暂存");
  await expect(
    page.getByRole("cell", { name: "009", exact: true }),
  ).toBeVisible();
  expect(requests).toBe(2);
});

test("system theme reaches dialogs and credentials stay in memory", async ({
  page,
}) => {
  await page.goto("/");
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "设置访问凭证" }).click();
  await page.getByLabel("访问凭证", { exact: true }).fill("temporary-token");
  await page.getByRole("button", { name: "应用", exact: true }).click();
  await page.locator(".upload-zone input[type=file]").setInputFiles({
    name: "主题预览.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("id,name\n008,主题测试"),
  });
  await page.getByRole("button", { name: "解析并暂存（1 个文件）" }).click();
  await expect(
    page.getByRole("heading", { name: "主题预览.csv", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "移除数据" }).click();
  const dialog = page.getByRole("dialog", { name: "移除数据" });
  await expect(
    dialog.getByRole("button", { name: "取消", exact: true }),
  ).toBeVisible();
  await expect(dialog).toHaveCSS("opacity", "1");
  await expect(dialog.locator(".ant-modal-container")).toHaveCSS(
    "background-color",
    "rgb(25, 33, 31)",
  );
  await page.screenshot({
    path: "test-results/intake-dark-dialog.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/intake-mobile-dialog.png",
    fullPage: true,
    animations: "disabled",
  });
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  const saved = await page.evaluate(() =>
    JSON.stringify({ ...localStorage, ...sessionStorage }),
  );
  expect(saved).not.toContain("temporary-token");
  await page.reload();
  await page.getByRole("button", { name: "设置访问凭证" }).click();
  await expect(page.getByLabel("访问凭证", { exact: true })).toHaveValue("");
});
