import { expect, test, type Page } from "@playwright/test";

async function upload(page: Page, name: string) {
  await page.goto("/");
  await page.locator(".upload-zone input[type=file]").setInputFiles({
    name,
    mimeType: "text/csv",
    buffer: Buffer.from("id,name\n001,测试客户\n"),
  });
  await page.getByRole("button", { name: "解析并暂存（1 个文件）" }).click();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
}

async function remove(page: Page, name: string) {
  await page.getByRole("button", { name: "移除数据", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "移除数据" });
  await expect(dialog).toContainText("不释放存储空间");
  await dialog.getByRole("button", { name: "确认移除" }).click();
  await expect(dialog).not.toBeVisible();
  await page
    .getByRole("radiogroup", { name: "接入记录范围" })
    .getByText("已移除", { exact: true })
    .click();
  await page.locator(".batch-row").filter({ hasText: name }).click();
  await expect(page.getByRole("button", { name: "恢复数据" })).toBeVisible();
}

test("removed data can be previewed, restored, then permanently deleted after confirmation", async ({
  page,
}) => {
  const name = "生命周期验收.csv";
  await upload(page, name);
  await remove(page, name);
  await expect(
    page.getByRole("cell", { name: "001", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "恢复数据" }).click();
  await page
    .getByRole("dialog", { name: "恢复数据" })
    .getByRole("button", { name: "确认恢复" })
    .click();
  await page
    .getByRole("radiogroup", { name: "接入记录范围" })
    .getByText("可用数据", { exact: true })
    .click();
  await page.locator(".batch-row").filter({ hasText: name }).click();
  await remove(page, name);
  await page.getByRole("button", { name: "彻底删除", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "彻底删除数据" });
  await expect(dialog).toContainText("未发现历史分析引用");
  await expect(dialog).toContainText("无法恢复");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(
    page.locator(".batch-row").filter({ hasText: name }),
  ).toHaveCount(1);
  await page.getByRole("button", { name: "彻底删除", exact: true }).click();
  await dialog.getByRole("button", { name: "彻底删除", exact: true }).click();
  await expect(
    page.locator(".batch-row").filter({ hasText: name }),
  ).toHaveCount(0);
});

test("referenced batches cannot be purged and reference service failures keep deletion disabled", async ({
  page,
}) => {
  const name = "引用保护验收.csv";
  await upload(page, name);
  await remove(page, name);
  await page.route("**/batches/*/references", (route) =>
    route.fulfill({ json: { count: 2, run_ids: ["protected-task-id"] } }),
  );
  await page.getByRole("button", { name: "彻底删除", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "彻底删除数据" });
  await expect(dialog).toContainText("2 个历史分析版本引用");
  await expect(
    dialog.getByRole("button", { name: "彻底删除", exact: true }),
  ).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/batch-delete-protected-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.unroute("**/batches/*/references");
  await page.route("**/batches/*/references", (route) =>
    route.fulfill({ status: 503, json: { detail: "暂时无法检查引用" } }),
  );
  await page.getByRole("button", { name: "彻底删除", exact: true }).click();
  await expect(dialog).toContainText("暂时无法检查引用");
  await expect(
    dialog.getByRole("button", { name: "彻底删除", exact: true }),
  ).toBeDisabled();
});
