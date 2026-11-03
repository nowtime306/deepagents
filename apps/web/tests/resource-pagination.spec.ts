import { expect, test } from "@playwright/test";

test("older repositories load once, failed pages retry and search reaches the server", async ({ page }) => {
  let fail = true, searches = 0;
  const repo = (id: string, name: string) => ({ id, name, slug: "sample/" + id, ref: "main", subdirectory: "", enabled: true, revision: 1 });
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url()), path = url.pathname.replace("/api/v1", "");
    let json: unknown = { items: [] };
    if (path === "/auth/me") json = { username: "分页测试", is_admin: true };
    if (path === "/models") json = { items: [{ id: "unused", name: "本机替身" }] };
    if (path === "/repositories") {
      if (url.searchParams.get("q")) { searches++; json = { items: [repo("older", "远期匹配")], next_cursor: null }; }
      else if (url.searchParams.get("before")) {
        if (fail) { fail = false; return route.fulfill({ status: 503, json: { error: { message: "页面读取失败" } } }); }
        json = { items: [repo("older", "旧仓库")], next_cursor: null };
      } else json = { items: [repo("new", "新仓库")], next_cursor: "older" };
    }
    await route.fulfill({ json });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "代码仓库", exact: true }).click();
  await expect(page.locator(".repository-card")).toHaveCount(1);
  await page.getByRole("button", { name: "加载更多仓库", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "页面读取失败" })).toBeVisible();
  await expect(page.locator(".repository-card")).toHaveCount(1);
  await page.getByRole("button", { name: "重试加载仓库", exact: true }).click();
  await expect(page.locator(".repository-card")).toHaveCount(2);
  await page.getByRole("textbox", { name: "搜索仓库", exact: true }).fill("远期匹配");
  await expect.poll(() => searches).toBeGreaterThan(0);
  await expect(page.locator(".repository-card")).toHaveCount(1);
  await expect(page.locator(".repository-card")).toContainText("远期匹配");
});

test("batch and occurrence load-more preserve the selected schedule and prior rows", async ({ page }) => {
  const schedule = { id: "schedule", name: "定时样例", mode: "ask", kind: "once", enabled: false, goal: "目标", timezone: "UTC", starts_at: "2026-01-01T00:00:00Z", budget: { model_calls: 1, token_limit: 2000 } };
  const batch = (id: string) => ({ id, name: "批次" + id, mode: "ask", status: "COMPLETED", items: [] });
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url()), path = url.pathname.replace("/api/v1", "");
    let json: unknown = { items: [] };
    if (path === "/auth/me") json = { username: "分页测试", is_admin: true };
    if (path === "/models") json = { items: [{ id: "unused", name: "本机替身" }] };
    if (path === "/automations") json = { items: [schedule] };
    if (path === "/batches") json = { items: [batch(url.searchParams.has("before") ? "旧" : "新")], next_cursor: url.searchParams.has("before") ? null : "batch-next" };
    if (path.endsWith("/occurrences")) json = { items: [{ id: url.searchParams.has("before") ? "old" : "new", scheduled_for: url.searchParams.has("before") ? "2026-01-01T00:00:00Z" : "2026-01-02T00:00:00Z", status: "SKIPPED_BUSY" }], next_cursor: url.searchParams.has("before") ? null : "history-next" };
    await route.fulfill({ json });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "自动任务", exact: true }).click();
  await page.getByRole("button", { name: "执行记录", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.locator("article")).toHaveCount(1);
  await dialog.getByRole("button", { name: "加载更早触发记录", exact: true }).click();
  await expect(dialog.locator("article")).toHaveCount(2);
  await page.getByRole("button", { name: "关闭记录", exact: true }).click();
  await page.getByRole("button", { name: "批量执行", exact: true }).click();
  await expect(page.getByText("批次新", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "加载更多批次", exact: true }).click();
  await expect(page.getByText("批次旧", { exact: true })).toBeVisible();
  await expect(page.getByText("批次新", { exact: true })).toBeVisible();
});
