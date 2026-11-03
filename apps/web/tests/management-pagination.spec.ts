import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";

test("management lists reach older items and search beyond the first page", async ({ page }) => {
  const paths: string[] = [];
  let finish!: () => void;
  const searchWait = new Promise<void>((resolve) => { finish = resolve; });
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url()), path = url.pathname.replace("/api/v1", "");
    paths.push(url.pathname + url.search);
    if (path === "/work-tasks" && url.searchParams.has("q")) await searchWait;
    let data: unknown = { items: [] };
    if (path === "/auth/me") data = { username: "分页验收", is_admin: true };
    if (path === "/models") data = { items: [{ id: "unused", name: "本机测试" }] };
    if (path === "/work-tasks") data = { items: [{ id: url.searchParams.size ? "old" : "new", title: url.searchParams.size ? "较早任务" : "最新任务", goal: "可搜索目标", revision: 1, updated_at: "2026-01-01" }],
      next_cursor: url.searchParams.size ? null : "next-page" };
    await route.fulfill({ json: data });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "任务工作台", exact: true }).click();
  await expect(page.getByText("最新任务", { exact: true })).toBeVisible();
  await page.getByLabel("搜索任务", { exact: true }).fill("较早");
  await expect(page.getByRole("button", { name: "加载更多任务", exact: true })).toHaveCount(0);
  await expect(page.getByRole("status")).toContainText("正在加载任务");
  finish();
  await expect(page.getByText("较早任务", { exact: true })).toBeVisible();
  await page.getByLabel("搜索任务", { exact: true }).fill("");
  await expect(page.getByText("最新任务", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "加载更多任务", exact: true }).click();
  await expect(page.getByText("较早任务", { exact: true })).toBeVisible();
  await expect(page.getByText("最新任务", { exact: true })).toBeVisible();
  await page.getByLabel("搜索任务", { exact: true }).fill("较早");
  await expect.poll(() => paths.some((path) => path.includes("q=") && !path.includes("before="))).toBe(true);
  await expect(page.getByText("较早任务", { exact: true })).toBeVisible();
});

test("older workflow drafts save and older runs are polled by exact ID", async ({ page }) => {
  const first = randomUUID(), old = randomUUID(), newestRun = randomUUID(), oldRun = randomUUID();
  const graph = { nodes: [{ id: "start", kind: "start", input: "input", parameters: {} }, { id: "end", kind: "end", input: "input", parameters: {} }], edges: [{ source: "start", target: "end" }] };
  let revision = 1, name = "较早流程", exact = false;
  const record = (id: string) => ({ id, name: id === old ? name : "最新流程", instructions: "", model_id: "unused", revision, definition: graph, layout: {} });
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url()), path = url.pathname.replace("/api/v1", "");
    let data: unknown = { items: [] };
    if (path === "/auth/me") data = { username: "历史验收", is_admin: true };
    if (path === "/models") data = { items: [{ id: "unused", name: "本机模型" }] };
    if (path === "/workflows") data = { items: [record(url.searchParams.size ? old : first)], next_cursor: url.searchParams.size ? null : "next-page" };
    if (path === `/workflows/${old}` && route.request().method() === "PUT") { revision++; name = route.request().postDataJSON().name; data = { id: old }; }
    if (path === `/workflows/${old}/runs`) {
      exact ||= url.searchParams.get("run_id") === oldRun;
      data = { items: [{ id: url.searchParams.size ? oldRun : newestRun, status: "SUCCEEDED", input: "输入", output: url.searchParams.size ? "旧记录结果" : "新记录", created_at: "2026-01-01", snapshot: { revision: 1, definition: graph } }],
        next_cursor: url.searchParams.size ? null : "older-runs" };
    }
    await route.fulfill({ json: data });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "工作流", exact: true }).click();
  await page.getByRole("button", { name: "加载更多工作流", exact: true }).click();
  await page.locator(".workflow-library-card").filter({ hasText: "较早流程" }).getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByRole("button", { name: "流程设置", exact: true }).click();
  await page.getByLabel("工作流名称", { exact: true }).fill("已修改旧流程");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("草稿已保存", { exact: true })).toBeVisible();
  await expect(page.locator(".workflow-studio-header")).toContainText("修订 2");
  await page.getByRole("button", { name: "运行历史", exact: true }).click();
  await page.getByRole("button", { name: "加载更早工作流运行", exact: true }).click();
  await page.locator(`.workflow-history-item[data-run-id="${oldRun}"] button`).click();
  await expect.poll(() => exact).toBe(true);
  await expect(page.getByRole("region", { name: "节点运行情况", exact: true })).toContainText("旧记录结果");
});
