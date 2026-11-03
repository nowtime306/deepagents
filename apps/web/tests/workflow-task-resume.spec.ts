import { expect, test } from "@playwright/test";
import { openCanvas } from "./workflow-test-ui";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const origin = "http://localhost:3100";
const metadata = resolve("../../services/api/.local/workflow-task-browser.json");

test("after service restart the fixed workflow resumes two approvals and executes the script exactly once", async ({ page }) => {
  test.setTimeout(120000);
  const fixture = JSON.parse(readFileSync(metadata, "utf8"));
  expect(fixture.stage).toBe("waiting_restart");
  const credentials = Object.fromEntries(readFileSync(resolve("../../services/api/.env.admin"), "utf8")
    .trim().split(/\r?\n/).map((line) => { const index = line.indexOf("="); return [line.slice(0, index), line.slice(index + 1)]; }));
  await page.goto(origin);
  await page.getByLabel("账号", { exact: true }).fill(credentials.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(credentials.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("button", { name: "退出登录", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "工作流", exact: true }).click();
  await openCanvas(page, fixture.name, "test");
  await page.getByRole("button", { name: "运行历史", exact: true }).click();
  await page.locator(`.workflow-history-item[data-run-id="${fixture.workflow_run}"] button`).click();
  await expect(page.locator(".workflow-run-summary")).toContainText("等待审批");
  await page.getByRole("button", { name: "查看任务与审批", exact: true }).click();
  await expect(page.getByText("流程人工确认", { exact: true })).toBeVisible();
  await expect(page.getByText("请在重启后确认，再执行代码", { exact: true })).toBeVisible();
  await page.getByText("查看输入内容 · 5字", { exact: true }).click();
  await page.getByRole("button", { name: "读取已校验的完整输入", exact: true }).click();
  await expect(page.locator(".workflow-approval-body pre")).toHaveText("HELLO");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: resolve("../../.local/workflow-approval-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "批准此操作", exact: true }).click();
  await expect(page.getByText("执行固定流程代码", { exact: true })).toBeVisible({ timeout: 60000 });
  await expect(page.getByText("修改参数后批准", { exact: true })).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", { name: "任务工作台", exact: true }).click();
  await page.locator(".run-history-card").filter({ hasText: fixture.name })
    .getByRole("button", { name: "执行详情", exact: true }).click();
  await expect(page.getByText("执行固定流程代码", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "批准此操作", exact: true }).click();
  await expect(page.locator(".work-execution-grid > main > pre.work-output")).toHaveText("HELLO-code", { timeout: 60000 });
  const root = await (await page.request.get(`${origin}/api/v1/work-runs/${fixture.run}`)).json();
  expect(root.status).toBe("COMPLETED");
  expect(root.result.text.trim()).toBe("HELLO-code");
  expect(root.result.verification.successful_code_nodes).toBe(1);
  const files = (await (await page.request.get(`${origin}/api/v1/work-runs/${fixture.run}/artifacts`)).json()).items;
  const counter = files.find((item: { path: string }) => item.path === "/workflow-ui-counter.txt");
  expect(await (await page.request.get(`${origin}/api/v1/work-artifacts/${counter.id}/download`)).text()).toBe("1");
  const usage = await (await page.request.get(`${origin}/api/v1/work-runs/${fixture.run}/usage`)).json();
  expect(usage.total.calls).toBe(0);
  const events = (await (await page.request.get(`${origin}/api/v1/workflows/${fixture.id}/runs/${fixture.workflow_run}/events`)).json()).items;
  expect(events.filter((item: { node_id: string; event: string }) => item.node_id === "upper" && item.event === "node.completed")).toHaveLength(1);
  fixture.stage = "completed";
  writeFileSync(metadata, JSON.stringify(fixture));
});
