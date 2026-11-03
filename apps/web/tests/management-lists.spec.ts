import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("management resources show lists first and organization dialogs preserve failed edits", async ({ page }) => {
  test.setTimeout(60000);
  const origin = "http://localhost:3100", headers = { Origin: origin };
  const name = `组织列表验收-${Date.now()}`; let organization = "";
  const credentials = Object.fromEntries(readFileSync(resolve("../../services/api/.env.admin"), "utf8").trim().split(/\r?\n/)
    .map((line) => { const index = line.indexOf("="); return [line.slice(0, index), line.slice(index + 1)]; }));
  await page.goto(origin); await page.getByLabel("账号", { exact: true }).fill(credentials.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(credentials.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台", exact: true }).click();
  await expect(page.getByRole("button", { name: "退出登录", exact: true })).toBeVisible();
  try {
    await page.getByRole("button", { name: "组织管理", exact: true }).click();
    await expect(page.getByLabel("组织名称", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "新建组织", exact: true }).click();
    const create = page.getByRole("dialog", { name: "新建组织", exact: true });
    await create.getByLabel("组织名称", { exact: true }).fill(name);
    const saved = page.waitForResponse((r) => r.url().endsWith("/api/v1/organizations") && r.request().method() === "POST");
    await create.getByRole("button", { name: "创建组织", exact: true }).click();
    const result = await saved; expect(result.status()).toBe(201); organization = (await result.json()).id;
    await expect(create).toHaveCount(0);
    await expect(page.getByRole("button", { name: "编辑成员", exact: true })).toHaveCount(0);
    await expect(page.getByLabel("成员账号", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "添加成员", exact: true }).click();
    const member = page.getByRole("dialog", { name: "添加或更新组织成员", exact: true });
    await member.getByLabel("成员账号", { exact: true }).fill("missing-member-" + Date.now());
    const rejected = page.waitForResponse((r) => r.url().endsWith(`/organizations/${organization}/members`) && r.request().method() === "PUT");
    await member.getByRole("button", { name: "添加或更新成员", exact: true }).click();
    expect((await rejected).status()).toBe(404); await expect(member.getByRole("alert")).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: "../../.local/management-member-mobile.png" });
    await page.keyboard.press("Escape"); await expect(member).toHaveCount(0);
    expect((await (await page.request.get(`${origin}/api/v1/organizations/${organization}/members`)).json()).items).toHaveLength(1);
    await page.getByRole("button", { name: "返回组织列表", exact: true }).click();
    await page.getByLabel("搜索组织", { exact: true }).fill(name);
    await expect(page.locator(".run-history-card").filter({ hasText: name })).toHaveCount(1);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: "../../.local/management-organizations.png" });
    await page.getByRole("button", { name: "设置", exact: true }).click();
    const routes = page.getByRole("region", { name: "模型路由设置", exact: true });
    await expect(routes.getByLabel("路由名称", { exact: true })).toHaveCount(0);
    await routes.getByRole("button", { name: "新增路由", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "新增路由", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "代码仓库", exact: true }).click();
    await expect(page.getByLabel("仓库名称", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "添加仓库", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "添加仓库", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "工作流", exact: true }).click();
    await page.getByRole("button", { name: "API 发布管理", exact: true }).click();
    await expect(page.getByLabel("调用方名称", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "新增 API Key", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "新增 API Key", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "运营管理", exact: true }).click();
    const billing = page.getByRole("region", { name: "费用与预算", exact: true });
    await expect(billing.getByLabel("周期金额", { exact: true })).toHaveCount(0);
    await billing.getByRole("button", { name: "新增预算", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "配置个人预算", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
  } finally {
    if (organization) expect((await page.request.delete(`${origin}/api/v1/organizations/${organization}`, { headers })).ok()).toBe(true);
  }
});
