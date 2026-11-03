import { openCanvas } from "./workflow-test-ui";
import { expect, test } from "@playwright/test";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const origin = "http://localhost:3100", headers = { Origin: origin };
const metadata = resolve("../../services/api/.local/workflow-task-browser.json");

test("Chinese workflow code editor starts a real zero-model task and leaves its approval for restart", async ({ page }) => {
  test.setTimeout(90000);
  expect(existsSync(metadata), "Clean the previous recorded task before creating another").toBe(false);
  const credentials = Object.fromEntries(readFileSync(resolve("../../services/api/.env.admin"), "utf8")
    .trim().split(/\r?\n/).map((line) => { const index = line.indexOf("="); return [line.slice(0, index), line.slice(index + 1)]; }));
  await page.goto(origin);
  await page.getByLabel("账号", { exact: true }).fill(credentials.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(credentials.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("button", { name: "退出登录", exact: true })).toBeVisible();
  const name = `固定流程重启验收-${Date.now()}`;
  const model = (await (await page.request.get(`${origin}/api/v1/models`)).json()).items[0].id;
  const graph = {
    execution: "work", node_budget: 5, model_calls: 1, token_limit: 10000,
    nodes: [{ id: "start", kind: "start" },
      { id: "upper", kind: "transform", parameters: { operation: "upper" } },
      { id: "review", kind: "approval", input: "upper", parameters: { prompt: "批准后继续" } },
      { id: "code", kind: "code", input: "review", parameters: { code: "print('draft')", timeout: 5 } },
      { id: "end", kind: "end", input: "code" }],
    edges: [{ source: "start", target: "upper" }, { source: "upper", target: "review" },
      { source: "review", target: "code" }, { source: "code", target: "end" }],
  };
  const response = await page.request.post(`${origin}/api/v1/workflows`, {
    headers, data: { name, model_id: model, definition: graph },
  });
  expect(response.status()).toBe(201);
  const id = (await response.json()).id;
  const fixture: Record<string, string> = { name, id, stage: "draft" };
  writeFileSync(metadata, JSON.stringify(fixture));
  await page.getByRole("button", { name: "工作流", exact: true }).click();
  await openCanvas(page, name);
  await expect(page.getByLabel("工作流执行方式")).toHaveValue("work");
  await page.locator('.react-flow__node[data-id="review"]').click();
  await page.getByLabel("流程审批说明").fill("请在重启后确认，再执行代码");
  await page.locator('.react-flow__node[data-id="code"]').click();
  const code = "import sys\nfrom pathlib import Path\np=Path('workflow-ui-counter.txt')\n"
    + "p.write_text((p.read_text() if p.exists() else '')+'1')\nprint(sys.stdin.read().strip()+'-code')";
  await page.getByLabel("流程Python代码").fill(code);
  await page.getByLabel("代码执行时间上限").fill("5");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.locator(".workflow-studio-header")).toContainText("修订 2");
  await openCanvas(page, name, "test");
  await page.getByLabel("测试输入", { exact: true }).fill("hello");
  const launched = page.waitForResponse((reply) => reply.url().endsWith(`/workflows/${id}/work-runs`));
  await page.getByRole("button", { name: "创建流程任务", exact: true }).click();
  const launchedResponse = await launched;
  expect(launchedResponse.status()).toBe(202);
  const root = await launchedResponse.json();
  Object.assign(fixture, { task: root.task_id, run: root.run_id, workflow_run: root.workflow_run_id, stage: "queued" });
  writeFileSync(metadata, JSON.stringify(fixture));
  await page.getByRole("button", { name: "查看任务与审批", exact: true }).click();
  await expect(page.getByText("这是固定流程任务。请在工作流页面明确选择输入、版本和网络授权后创建新运行。", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "待审批操作", exact: true })).toBeVisible();
  await expect(page.getByText("流程人工确认", { exact: true })).toBeVisible({ timeout: 60000 });
  await expect(page.getByText("请在重启后确认，再执行代码", { exact: false }).first()).toBeVisible();
  const usage = await (await page.request.get(`${origin}/api/v1/work-runs/${root.run_id}/usage`)).json();
  expect(usage.total.calls).toBe(0);
  fixture.stage = "waiting_restart";
  writeFileSync(metadata, JSON.stringify(fixture));
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: resolve("../../.local/workflow-approval-mobile.png"), fullPage: true });
});
