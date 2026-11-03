import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const origin = "http://localhost:3100";
const metadata = resolve("../../services/api/.local/repository-browser-fixture.json");

test("actual native Git task approves one fixed script, persists deletion and fits mobile", async ({ page }) => {
  test.setTimeout(120000);
  test.skip(!existsSync(metadata), "Prepare the named zero-model native repository fixture first");
  const fixture = JSON.parse(readFileSync(metadata, "utf8"));
  expect(fixture.name).toMatch(/^本机Git恢复验收-/);
  const values = Object.fromEntries(readFileSync(resolve("../../services/api/.env.admin"), "utf8")
    .trim().split(/\r?\n/).map((line) => { const i = line.indexOf("="); return [line.slice(0, i), line.slice(i + 1)]; }));
  await page.goto(origin);
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("button", { name: "退出登录", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "任务工作台", exact: true }).click();
  await page.locator(".run-history-card").filter({ hasText: fixture.name })
    .getByRole("button", { name: "执行详情", exact: true }).click();
  const root = await (await page.request.get(`${origin}/api/v1/work-runs/${fixture.run}`)).json();
  expect(root.model_calls).toBe(0);
  if (root.status === "WAITING_APPROVAL") {
    const approvals = (await (await page.request.get(`${origin}/api/v1/work-runs/${fixture.run}/approvals`)).json()).items;
    const pending = approvals.filter((a: { decision: string | null }) => !a.decision);
    expect(pending).toHaveLength(1);
    const code = pending[0].payload.action_requests[0].args.code;
    expect(createHash("sha256").update(code).digest("hex")).toBe(fixture.code_digest);
    await expect(page.getByText("执行固定流程代码", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "批准此操作", exact: true }).click();
  } else expect(["PENDING", "RUNNING", "COMPLETED"]).toContain(root.status);
  await expect(page.locator(".work-execution-grid > main > pre.work-output"))
    .toContainText("git-workspace-ok", { timeout: 90000 });
  const review = page.getByRole("region", { name: "代码差异审阅", exact: true });
  await expect(review).toContainText("删除 · delete.txt");
  await review.getByText("修改 · main.py", { exact: true }).click();
  await expect(review).toContainText("+print('git-workspace-ok')");
  await review.getByText("已批准命令与运行证据（1）", { exact: true }).click();
  await expect(review).toContainText("退出码 0");
  const files = (await (await page.request.get(`${origin}/api/v1/work-runs/${fixture.run}/artifacts`)).json()).items;
  const counter = files.find((f: { path: string }) => f.path === "/git-fixture-counter.txt");
  expect(await (await page.request.get(`${origin}/api/v1/work-artifacts/${counter.id}/download`)).text()).toBe("1");
  expect((await (await page.request.get(`${origin}/api/v1/work-runs/${fixture.run}/usage`)).json()).total.calls).toBe(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await review.evaluate((e) => e.scrollWidth <= e.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: resolve("../../.local/repository-native-mobile.png"), fullPage: true });
});
