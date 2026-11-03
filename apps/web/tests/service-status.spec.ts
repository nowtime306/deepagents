import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("actual managed service status recovers from read failure and fits narrow layout", async ({ page }) => {
  const origin = "http://localhost:3100";
  const values = Object.fromEntries(readFileSync(resolve("../../services/api/.env.admin"), "utf8")
    .trim().split(/\r?\n/).map((line) => { const i = line.indexOf("="); return [line.slice(0, i), line.slice(i + 1)]; }));
  await page.goto(origin);
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("button", { name: "退出登录", exact: true })).toBeVisible();
  let unavailable = false;
  await page.route("**/api/v1/operations/services", async (route) => {
    if (unavailable) return route.fulfill({ status: 503, json: { error: { code: "SERVICE_RESTARTING", message: "正在恢复" } } });
    return route.continue();
  });
  await page.getByRole("button", { name: "运营管理", exact: true }).click();
  const panel = page.getByRole("region", { name: "平台后台状态", exact: true });
  await expect(panel.getByText(/1个实例在线$/)).toHaveCount(4, { timeout: 20000 });
  const status = await page.request.get(`${origin}/api/v1/operations/services`);
  expect(status.ok()).toBeTruthy(); expect(await status.text()).not.toContain("backend_id");
  unavailable = true;
  await expect(panel).toContainText("后台状态暂不可用", { timeout: 10000 });
  await expect(panel.getByText(/连接正在恢复$/)).toHaveCount(4);
  unavailable = false;
  await expect(panel.getByText(/1个实例在线$/)).toHaveCount(4, { timeout: 10000 });
  await panel.getByText("最近的启动与恢复记录", { exact: true }).click();
  await expect(panel).toContainText("已启动");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
});
