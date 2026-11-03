import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test.beforeEach(async ({ page }) => {
  const values = Object.fromEntries(readFileSync(resolve("../../services/api/.env.admin"), "utf8")
    .trim().split(/\r?\n/).map((line) => { const i = line.indexOf("="); return [line.slice(0, i), line.slice(i + 1)]; }));
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台", exact: true }).click();
  await expect(page.getByRole("button", { name: "退出登录", exact: true })).toBeVisible();
});

test("grouped workspace navigation, named usage and compact audit preserve actual records", async ({ page }) => {
  const navigation = page.getByRole("navigation", { name: "主导航", exact: true });
  for (const title of ["工作空间", "资源与能力", "管理与配置"]) await expect(navigation.getByText(title, { exact: true })).toBeVisible();
  const operation = navigation.getByRole("button", { name: "运营管理", exact: true });
  await operation.focus();
  await page.keyboard.press("Enter");
  await expect(operation).toHaveAttribute("aria-current", "page");
  const calls = page.getByRole("region", { name: "模型调用记录", exact: true });
  await expect(calls.getByRole("row")).not.toHaveCount(1);
  const models = await (await page.request.get("http://localhost:3100/api/v1/admin/models")).json();
  const usage = await (await page.request.get("http://localhost:3100/api/v1/operations/usage")).json();
  const known = usage.items.find((item: { model_id: string }) => models.items.some((model: { id: string }) => model.id === item.model_id));
  expect(known).toBeTruthy();
  const name = models.items.find((model: { id: string }) => model.id === known.model_id).name;
  await expect(calls.getByText(name, { exact: true }).first()).toBeVisible();
  expect(await calls.innerText()).not.toContain(known.model_id);
  const audit = page.getByRole("region", { name: "操作审计记录", exact: true });
  await expect(audit.getByRole("row")).not.toHaveCount(1);
  expect(await audit.evaluate((element) => element.clientHeight <= 480)).toBe(true);
  const jump = page.getByRole("navigation", { name: "运营区块", exact: true });
  await jump.getByRole("link", { name: "操作审计", exact: true }).click();
  await expect(audit).toBeInViewport();
  await jump.getByRole("link", { name: "用量概览", exact: true }).click();
  await expect(page.getByRole("heading", { name: "累计用量与估算", exact: true })).toBeInViewport();
  await page.getByRole("heading", { name: "运营管理", exact: true }).click();
  await page.screenshot({ path: "../../.local/ui-refresh-operations-desktop.png", fullPage: true });
  await page.getByRole("button", { name: "收起侧边栏", exact: true }).click();
  await expect(operation).toBeVisible();
  await page.getByRole("button", { name: "展开侧边栏", exact: true }).click();
  await navigation.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "模型管理", exact: true })).toBeVisible();
  await page.screenshot({ path: "../../.local/ui-refresh-settings-desktop.png", fullPage: true });
  await navigation.getByRole("button", { name: "账号管理", exact: true }).click();
  await expect(page.getByRole("heading", { name: "账号与权限", exact: true })).toBeVisible();
  await page.screenshot({ path: "../../.local/ui-refresh-accounts-desktop.png", fullPage: true });
});

test("mobile navigation closes after selection and long unknown records remain readable", async ({ page }) => {
  await page.route("**/api/v1/operations/usage", (route) => route.fulfill({ json: {
    items: [{ call_id: "local-ui", run_id: "local-ui", model_id: "unavailable-model", mode: "ask", status: "UNKNOWN", input_tokens: null, output_tokens: null, estimated_cost: null, currency: null, created_at: "2026-11-01T00:00:00Z" }],
    summary: [{ currency: null, estimated_cost: null, input_tokens: null, output_tokens: null, calls: 1, unknown_calls: 1, unpriced_calls: 1 }],
  } }));
  await page.route("**/api/v1/operations/audit", (route) => route.fulfill({ json: {
    items: [{ id: 1, method: "POST", resource_path: `/api/v1/workflows/${"long-path-".repeat(60)}`, status_code: 503, created_at: "2026-11-01T00:00:00Z" }], next_before: null,
  } }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "展开侧边栏", exact: true }).click();
  await page.getByRole("navigation", { name: "主导航", exact: true }).getByRole("button", { name: "运营管理", exact: true }).click();
  await expect(page.locator(".platform-shell")).not.toHaveClass(/sidebar-open/);
  const calls = page.getByRole("region", { name: "模型调用记录", exact: true });
  await expect(calls).toContainText("用量未知");
  await expect(calls).toContainText("金额未计算");
  await expect(page.locator(".operations-metric strong").filter({ hasText: "—" })).toHaveCount(2);
  const audit = page.getByRole("region", { name: "操作审计记录", exact: true });
  await expect(audit).toContainText("未完成 · 503");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await audit.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
  await page.screenshot({ path: "../../.local/ui-refresh-mobile.png", fullPage: true });
});

test("a slow model refresh after save does not erase newly entered filters", async ({ page }) => {
  let hold = false;
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  const model = { id: "ui-only-model", name: "old-ui-filter", model: "ui-only", base_url: "http://ui-only.invalid/v1",
    api_key_configured: false, enabled: true, is_default: false, max_output_tokens: null, temperature: null,
    supports_images: false, supports_tools: null, revision: 1, connection_status: "not_tested",
    kind: "chat", provider: "custom", protocol: "openai", probe_hint: null };
  await page.route("**/api/v1/admin/models", async (route) => {
    expect(route.request().method()).toBe("GET");
    if (hold) await delayed;
    await route.fulfill({ json: { items: [model] } });
  });
  await page.route("**/api/v1/admin/models/ui-only-model", async (route) => {
    expect(route.request().method()).toBe("PUT");
    hold = true;
    await route.fulfill({ json: { ...model, revision: 2 } });
  });
  try {
    await page.getByRole("button", { name: "设置", exact: true }).click();
    const search = page.getByLabel("搜索模型", { exact: true });
    await search.fill("old-ui-filter");
    await page.getByRole("article").getByRole("button", { name: "编辑", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "保存模型", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await search.fill("new-filter-during-refresh");
    await page.getByLabel("筛选模型类型", { exact: true }).selectOption("chat");
    release();
    await expect(page.getByText("模型配置已保存，使用范围见模型类型说明", { exact: true })).toBeVisible();
    await expect(search).toHaveValue("new-filter-during-refresh");
    await expect(page.getByLabel("筛选模型类型", { exact: true })).toHaveValue("chat");
    await expect(page.getByRole("article")).toHaveCount(0);
  } finally { release(); }
});
