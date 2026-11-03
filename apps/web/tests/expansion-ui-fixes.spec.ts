import { expect, test } from "@playwright/test";

test.use({ baseURL: "http://127.0.0.1:3101", trace: "off", screenshot: "off", video: "off" });

async function fixture(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown = { items: [] };
    if (path.endsWith("/auth/me")) data = { username: "界面自验", is_admin: true, permissions: [] };
    if (path.endsWith("/admin/models/catalog")) data = { kinds: [], providers: [], protocols: [] };
    if (path.endsWith("/admin/identity/providers")) data = { items: [
      { id: "a", name: "研发中心", issuer: "https://DEV.invalid", enabled: true, client_id: "a", allow_registration: false, allowed_domains: [] },
      { id: "b", name: "财务中心", issuer: "https://finance.invalid", enabled: false, client_id: "b", allow_registration: false, allowed_domains: [] },
    ] };
    if (path.endsWith("/operations/monitoring")) data = { sampled_at: new Date().toISOString(), stale: false, age_seconds: 0, snapshot: null,
      runs: { total: 0, succeeded: 0, failed: 0, duration_samples: 0, p95_seconds: null },
      window: { from: new Date().toISOString(), to: new Date().toISOString(), usage: { calls: 1, input_tokens: "10", output_tokens: "2", unknown_tokens: 0 },
        costs: [{ currency: "USD", estimated_cost: "999999999999999.999999999", unknown_calls: 0 }], unknown_cost_calls: 0 } };
    await route.fulfill({ json: data });
  });
}

test("provider list supports search, status and empty state without losing editor", async ({ page }) => {
  await fixture(page); await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const list = page.getByRole("list", { name: "认证来源列表" });
  await expect(list.getByRole("listitem")).toHaveCount(2);
  await page.getByLabel("搜索认证来源", { exact: true }).fill("dev.INVALID");
  await expect(list.getByRole("listitem")).toHaveCount(1);
  await page.getByLabel("认证来源状态", { exact: true }).selectOption("disabled");
  await expect(page.getByText("未找到符合条件的认证来源")).toBeVisible();
  await page.getByLabel("搜索认证来源", { exact: true }).fill(" 财务 ");
  await expect(list.getByRole("button", { name: "编辑财务中心", exact: true })).toBeVisible();
  await list.getByRole("button", { name: "编辑财务中心", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "编辑认证来源" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test("valid large decimal cost fits the mobile monitoring cards", async ({ page }) => {
  await fixture(page); await page.goto("/");
  await page.getByRole("button", { name: "监测大盘", exact: true }).click();
  await expect(page.getByText("USD 999999999999999.999999999", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
