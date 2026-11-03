import { expect, test, type Page } from "@playwright/test";

// Auth and every API are replaced. These cases must only run after browser access is authorized.
async function fixture(page: Page, readonly = false) {
  const calls: string[] = [], retries: unknown[] = [];
  let historyError = false;
  const oldTarget = { kind: "webhook", host: "old.example.invalid", identity: "a".repeat(64) };
  const currentTarget = { kind: "webhook", host: "new.example.invalid", identity: "b".repeat(64) };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname.replace("/api/v1", "");
    calls.push(`${request.method()} ${path}`);
    let data: unknown = { items: [] };
    if (path === "/auth/me") data = { id: "owner", username: "本地追溯验证", is_admin: !readonly,
      permissions: readonly ? ["chat.read", "knowledge.read", "notifications.read", "automation.read"] : undefined };
    if (path === "/models") data = { items: [] };
    if (path === "/capabilities/knowledge") data = { items: [{ id: "kb-a", name: "配置追溯资料", description: "",
      documents: 0, embedding_model_id: null, rerank_model_id: null, keyword_min_coverage: 0 }] };
    if (path === "/capabilities/bindings") data = { knowledge_ids: [], mcp_ids: [], skill_ids: [] };
    if (path.endsWith("/processing")) data = { chunk_size: 800, chunk_overlap: 120, revision: 1 };
    if (path === "/resource-configurations/knowledge/kb-a/changes") {
      if (historyError) return route.fulfill({ status: 403, json: { error: { message: "当前账号无权查看修改记录" } } });
      data = { items: [{ id: 12, resource_name: "配置追溯资料", operation: "UPDATE", before_revision: 1, after_revision: 2,
        created_at: "2026-11-03T02:00:00Z", actor_id: "owner", source: "user", change_id: "same-operation-001",
        baseline: false, categories: ["行为变化"], changes: { keyword_min_coverage: { before: 0, after: 0.4 },
          command_json: { before: null, after: { redacted: true, digest: "f".repeat(64) } } } },
      { id: 11, resource_name: "配置追溯资料", operation: "BASELINE", before_revision: null, after_revision: 1,
        created_at: "2026-11-03T01:00:00Z", actor_id: null, source: "system:migration082", change_id: "migration082",
        baseline: true, categories: [], changes: {} }] };
    }
    if (path === "/notification-channels") data = { items: [{ id: "channel", name: "任务状态", kind: "webhook",
      host_hint: currentTarget.host, has_secret: true, enabled: true, allow_private: false, revision: 2 }] };
    if (path === "/notifications/deliveries") data = { items: [
      { id: "paused", channel_name: "任务状态", status: "PAUSED", attempts: 1,
        error_code: "CHANNEL_TARGET_CHANGED_REVIEW_REQUIRED", created_at: "2026-11-03T02:00:00Z",
        target_summary: oldTarget, current_target_summary: currentTarget },
      { id: "unknown", channel_name: "任务状态", status: "UNKNOWN", attempts: 1, error_code: null,
        created_at: "2026-11-03T02:00:00Z", target_summary: oldTarget, current_target_summary: oldTarget },
      { id: "retry", channel_name: "任务状态", status: "SUCCEEDED", attempts: 1, error_code: null,
        created_at: "2026-11-03T02:00:00Z", retry_of: "paused", target_summary: currentTarget,
        current_target_summary: currentTarget },
      { id: "legacy", channel_name: "任务状态", status: "UNKNOWN", attempts: 1, error_code: null,
        created_at: "2026-11-03T02:00:00Z", target_summary: null, current_target_summary: currentTarget },
    ] };
    if (path.endsWith("/retry") && request.method() === "POST") {
      retries.push(request.postDataJSON()); data = { queued: true, id: "retry-new" };
    }
    await route.fulfill({ json: data });
  });
  return { calls, retries, failHistory: () => { historyError = true; } };
}
async function history(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "能力中心", exact: true }).click();
  await page.locator("article").filter({ has: page.getByRole("heading", { name: "配置追溯资料", exact: true }) })
    .getByRole("button", { name: "管理文档与检索", exact: true }).click();
  await page.getByRole("tab", { name: "配置", exact: true }).click();
}
async function notifications(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "自动任务", exact: true }).click();
  await page.getByRole("button", { name: "通知", exact: true }).click();
}

test("readonly history loads on expansion, shows differences and baseline without write actions", async ({ page }) => {
  const state = await fixture(page, true);
  await history(page);
  expect(state.calls.some((call) => call.includes("/changes"))).toBe(false);
  await page.getByLabel("配置追溯资料修改记录", { exact: true }).click();
  const panel = page.locator(".configuration-history");
  await expect(panel.getByText("关键词最低覆盖率", { exact: true })).toBeVisible();
  await expect(panel.getByText("同次修改标识：same-operation-001")).toBeVisible();
  await expect(panel.getByText("系统来源：system:migration082")).toBeVisible();
  await expect(panel.getByText("上线时建立的基线，无法还原此前的修改。")).toBeVisible();
  await expect(panel.getByText("内容已隐藏 · 摘要 ffffffffffff")).toBeVisible();
  expect(state.calls.every((call) => call.startsWith("GET "))).toBe(true);
  await expect(page.getByRole("button", { name: "保存检索策略", exact: true })).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
});

test("history access failure hides stale evidence and provides retry without changing settings", async ({ page }) => {
  const state = await fixture(page);
  await history(page);
  await page.getByLabel("配置追溯资料修改记录", { exact: true }).click();
  await expect(page.locator(".configuration-change")).toHaveCount(2);
  state.failHistory();
  await page.getByRole("button", { name: "刷新记录", exact: true }).click();
  await expect(page.locator(".configuration-history").getByRole("alert")).toHaveText("当前账号无权查看修改记录");
  await expect(page.locator(".configuration-change")).toHaveCount(0);
});

test("changed delivery target needs explicit visible confirmation and retains retry relationship", async ({ page }) => {
  const state = await fixture(page);
  await notifications(page);
  const paused = page.locator("#notification-delivery-paused");
  await expect(paused).toContainText("old.example.invalid");
  await expect(paused).toContainText("new.example.invalid");
  expect(state.retries).toEqual([]);
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("old.example.invalid");
    expect(dialog.message()).toContain("new.example.invalid");
    expect(dialog.message()).toContain("bbbbbbbbbbbb");
    await dialog.dismiss();
  });
  await paused.getByRole("button", { name: "核对目标并重新投递", exact: true }).click();
  expect(state.retries).toEqual([]);
  page.once("dialog", (dialog) => dialog.accept());
  await paused.getByRole("button", { name: "核对目标并重新投递", exact: true }).click();
  await expect.poll(() => state.retries).toEqual([{ acknowledge_duplicate: true, confirm_target: true, expected_target: "b".repeat(64) }]);
  await expect(page.locator("#notification-delivery-retry").getByRole("link", { name: "paused", exact: true }))
    .toHaveAttribute("href", "#notification-delivery-paused");
  await expect(page.locator("#notification-delivery-legacy").getByRole("button")).toBeDisabled();
});

test("unknown delivery does not resend until acknowledged and keeps same target", async ({ page }) => {
  const state = await fixture(page);
  await notifications(page);
  await page.waitForTimeout(100);
  expect(state.retries).toEqual([]);
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#notification-delivery-unknown").getByRole("button", { name: "核对目标并重新投递", exact: true }).click();
  await expect.poll(() => state.retries).toEqual([{ acknowledge_duplicate: true, confirm_target: false, expected_target: "a".repeat(64) }]);
});


test("notification reader can inspect target history without edit or resend controls", async ({ page }) => {
  const state = await fixture(page, true);
  await notifications(page);
  await expect(page.getByText("原接收目标：签名 Webhook · old.example.invalid · 目标标识 aaaaaaaaaaaa").first()).toBeVisible();
  await expect(page.getByLabel("任务状态修改记录", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "添加通知通道", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "编辑通道", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "核对目标并重新投递", exact: true })).toHaveCount(0);
  expect(state.retries).toEqual([]);
});
