import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const origin = "http://localhost:3100";
const headers = { Origin: origin };
async function login(page: Page) {
  const values = Object.fromEntries(readFileSync(resolve(process.cwd(), "../../services/api/.env.admin"), "utf8")
    .trim().split(/\r?\n/).map((line) => { const i = line.indexOf("="); return [line.slice(0, i), line.slice(i + 1)]; }));
  await page.goto(origin);
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("button", { name: "退出登录", exact: true })).toBeVisible();
}

test("owned repository settings preserve/clear credentials and narrow layout", async ({ page }) => {
  let identifier = "";
  const name = `仓库页面验收-${Date.now()}`;
  const secret = "fake-local-browser-token-only";
  try {
    await login(page);
    await page.getByRole("button", { name: "代码仓库", exact: true }).click();
    await page.getByRole("button", { name: "添加仓库", exact: true }).click();
    await page.getByLabel("仓库名称", { exact: true }).fill(name);
    await page.getByLabel("GitHub仓库", { exact: true }).fill("example/local-ui-test");
    await page.getByLabel("仓库访问令牌", { exact: true }).fill(secret);
    const created = page.waitForResponse((r) => r.url().endsWith("/api/v1/repositories") && r.request().method() === "POST");
    await page.getByRole("button", { name: "保存仓库", exact: true }).click();
    identifier = (await (await created).json()).id;
    expect(identifier).toBeTruthy();
    const card = page.locator(".repository-card").filter({ hasText: name });
    await expect(card).toContainText("已保存令牌");
    const listing = await (await page.request.get(`${origin}/api/v1/repositories`)).text();
    expect(listing).not.toContain(secret);
    await card.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(page.getByLabel("仓库访问令牌", { exact: true })).toHaveValue("");
    await page.getByRole("button", { name: "保存仓库", exact: true }).click();
    await expect(page.locator(".repository-form")).toHaveCount(0);
    await expect(card).toContainText("已保存令牌");
    await card.getByRole("button", { name: "编辑", exact: true }).click();
    await page.getByLabel("清除已保存令牌", { exact: true }).check();
    await page.getByRole("button", { name: "保存仓库", exact: true }).click();
    await expect(card).toContainText("公开访问");
    await page.setViewportSize({ width: 390, height: 844 });
    await card.getByRole("button", { name: "编辑", exact: true }).click();
    expect(await page.locator(".repository-panel").evaluate((e) => e.scrollWidth <= e.clientWidth + 1)).toBe(true);
    await expect(page.getByRole("button", { name: "保存仓库", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "取消", exact: true }).click();
  } finally {
    if (identifier) {
      const result = await page.request.delete(`${origin}/api/v1/repositories/${identifier}`, { headers });
      expect(result.ok()).toBeTruthy();
    }
  }
});

test("repository review approval, unknown check and draft PR use an explicit local protocol stand-in", async ({ page }) => {
  const run = "repository-ui-run", task = "repository-ui-task";
  const sha = "a".repeat(40), hash = "b".repeat(64);
  const repository = { slug: "example/project", ref: "main", sha, branch: "owner-agent/ui-task", status: "COMPLETED",
    snapshot_id: "snapshot", commands: [{ name: "execute", args: { command: "pytest -q" }, status: "SUCCEEDED", exit_code: 0, output: "2 passed" }],
    changes: [{ path: "main.py", kind: "modified", mode: "100644", sha: "c".repeat(40), size: 20,
      artifact_id: null, diff: "--- a/main.py\n+++ b/main.py\n-print(1)\n+print(2)\n" }] };
  const actions: { id: string; kind: string; status: string; digest: string; payload: typeof repository & { title?: string; body?: string };
    expires_at: string; result: { url?: string } }[] = [];
  let pushes = 0, prs = 0, checks = 0;
  const savedEvidence: { id: string; created_at: string; data: { source: string; issue: { number: number; title: string; body: string } } }[] = [];
  let evidenceReads = 0;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let data: unknown;
    if (path === "/work-tasks") data = { items: [{ id: task, title: "仓库审阅验收", revision: 1, goal: "本机替身", updated_at: new Date().toISOString() }] };
    else if (path === `/work-tasks/${task}/runs`) data = { items: [{ id: run, status: "COMPLETED", created_at: new Date().toISOString() }] };
    else if (path === `/work-runs/${run}`) data = { id: run, status: "COMPLETED", result: { text: "测试完成" }, model_calls: 0, used_tokens: 0, environment: "coding" };
    else if (path === `/work-runs/${run}/repository`) data = { repository, actions, evidence: savedEvidence };
    else if (path === `/work-runs/${run}/repository/evidence`) {
      expect(route.request().method()).toBe("POST"); evidenceReads++;
      expect(route.request().postDataJSON().issue).toBe(4);
      const value = { source: "https://github.com/example/project", issue: { number: 4, title: "持久Issue证据", body: "本机替身外部资料" } };
      savedEvidence.unshift({ id: "evidence-local", created_at: new Date().toISOString(), data: value }); data = value;
    }
    else if (path === `/work-runs/${run}/repository/prepare`) {
      const body = route.request().postDataJSON();
      actions.unshift({ id: `action-${actions.length}`, kind: body.kind, status: "PREPARED", digest: hash,
        payload: { ...repository, title: body.title, body: body.body }, expires_at: new Date(Date.now() + 900000).toISOString(), result: {} });
      data = actions[0];
    } else if (path.startsWith("/repository-actions/") && path.endsWith("/decision")) {
      const action = actions.find((a) => path.includes(a.id))!;
      const body = route.request().postDataJSON();
      expect(body.digest).toBe(hash);
      if (!body.approve) action.status = "REJECTED";
      else if (action.kind === "push") { pushes++; action.status = "UNKNOWN"; }
      else { prs++; action.status = "SUCCEEDED"; action.result = { url: "https://github.com/example/project/pull/7" }; }
      data = { status: action.status };
    } else if (path.startsWith("/repository-actions/") && path.endsWith("/check")) {
      checks++; actions.find((a) => path.includes(a.id))!.status = "SUCCEEDED"; data = { status: "SUCCEEDED" };
    } else if (path.startsWith(`/work-runs/${run}/`) && /\/(approvals|artifacts|operations|events)$/.test(path)) data = { items: [] };
    else if (path === "/repositories/options") data = { items: [] };
    else if (path === `/work-runs/${run}/recovery`) data = { sessions: [], job: null };
    else return route.continue();
    return route.fulfill({ json: data });
  });
  await login(page);
  await page.getByRole("button", { name: "任务工作台", exact: true }).click();
  await page.getByRole("button", { name: "执行详情", exact: true }).click();
  const panel = page.getByRole("region", { name: "代码差异审阅", exact: true });
  await expect(panel.getByRole("heading", { name: "代码与发布", exact: true })).toBeVisible();
  await panel.getByText("修改 · main.py", { exact: true }).click();
  await expect(panel).toContainText("+print(2)");
  await panel.getByText("已批准命令与运行证据（1）", { exact: true }).click();
  await expect(panel).toContainText("2 passed");
  await panel.getByRole("button", { name: "准备发布审阅", exact: true }).click();
  expect(pushes).toBe(0);
  await panel.getByText("查看本次批准内容", { exact: true }).click();
  await expect(panel).toContainText(hash);
  await panel.getByRole("button", { name: "批准发布", exact: true }).click();
  await expect(panel).toContainText("结果未知，需核查");
  expect(pushes).toBe(1);
  await panel.getByRole("button", { name: "只读核查远端", exact: true }).click();
  await expect(panel).toContainText("发布任务分支 · 已完成");
  expect(checks).toBe(1); expect(pushes).toBe(1);
  await panel.getByLabel("PR标题", { exact: true }).fill("修复代码");
  await panel.getByLabel("PR说明", { exact: true }).fill("本机协议替身验收");
  await panel.getByRole("button", { name: "准备草稿PR审阅", exact: true }).click();
  expect(prs).toBe(0);
  await panel.getByRole("button", { name: "批准创建PR", exact: true }).click();
  await expect(panel.getByRole("link", { name: "查看远端结果", exact: true })).toHaveAttribute("href", "https://github.com/example/project/pull/7");
  expect(prs).toBe(1);
  await panel.getByText("Issue与CI证据", { exact: true }).click();
  await panel.getByLabel("Issue编号", { exact: true }).fill("4");
  await panel.getByRole("button", { name: "读取并保存远端证据", exact: true }).click();
  await expect(panel).toContainText("已保存的证据历史（1）"); expect(evidenceReads).toBe(1);
  await page.reload();
  await page.getByRole("button", { name: "任务工作台", exact: true }).click();
  await page.getByRole("button", { name: "执行详情", exact: true }).click();
  await panel.getByText("Issue与CI证据", { exact: true }).click();
  await panel.getByText("已保存的证据历史（1）", { exact: true }).click();
  await expect(panel).toContainText("持久Issue证据"); expect(evidenceReads).toBe(1);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await panel.evaluate((e) => e.scrollWidth <= e.clientWidth + 1)).toBe(true);
});
