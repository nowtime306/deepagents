import { expect, test } from "@playwright/test";

function snapshot() {
  return { sampled_at: new Date().toISOString(), stale: false, age_seconds: 1,
    snapshot: { services: ["api", "ask", "work", "ops"].map(kind => ({ kind, online: 1, last_heartbeat: new Date().toISOString() })),
      queues: [{ mode: "work", status: "PENDING", count: 2, oldest_seconds: 35 }],
      host: { cpu_percent: 15, memory_available: 8 * 1024 ** 3, memory_total: 32 * 1024 ** 3,
        disk_free: 100 * 1024 ** 3, disk_total: 200 * 1024 ** 3 },
      usage: [{ calls: 1, input_tokens: 12, output_tokens: 3, unknown_tokens: 0 }],
      costs: [{ currency: "CNY", estimated_cost: "0.02", unknown_calls: 0 }], environments: [], unknown_cost_calls: 0 },
    runs: { total: 3, succeeded: 2, failed: 1, duration_samples: 3, p95_seconds: 7.5 } };
}

test("monitoring shows real-source states, editable rules, acknowledgement distinct from recovery and mobile", async ({ page }) => {
  const sample = snapshot();
  let rule: Record<string, unknown> | null = null, acknowledged = false;
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let data: unknown = { items: [], summary: [] };
    if (path === "/auth/me") data = { id: "00000000-0000-4000-8000-000000000001", username: "监测自验", is_admin: true, permissions: [] };
    else if (path === "/models") data = { items: [{ id: "local", name: "本机替身", kind: "chat", protocol: "openai" }] };
    else if (path === "/operations/monitoring") {
      const hours = Number(new URL(route.request().url()).searchParams.get("hours") || 1);
      data = { ...sample, window: { from: sample.sampled_at, to: sample.sampled_at,
        usage: { calls: hours, input_tokens: hours * 100, output_tokens: hours * 10, unknown_tokens: 1 },
        costs: [{ currency: "CNY", estimated_cost: String(hours), unknown_calls: 1 }], unknown_cost_calls: 1 } };
    }
    else if (path.endsWith("/monitoring/series")) data = { items: [{ sampled_at: sample.sampled_at, snapshot: sample.snapshot }] };
    else if (path.endsWith("/monitoring/instances")) data = { items: [{ id: "api-observation", kind: "api", node: "本机节点", fault_domain: "单机故障域", release: "0.1.0-dev", live: true, ready: false, stale: false, heartbeat_at: sample.sampled_at }] };
    else if (path.endsWith("/monitoring/run-trend")) data = { items: [{ time: sample.sampled_at, started: 3, succeeded: 2, failed: 1 }] };
    else if (path.endsWith("/monitoring/errors")) data = { items: [{ id: "workflow-failure", mode: "workflow", error_code: "INPUT_INVALID", can_open: true, resource_id: "own-flow", created_at: sample.sampled_at }, { id: "foreign-work-failure", mode: "work", error_code: "FAILED", can_open: false, resource_id: null, created_at: sample.sampled_at }] };
    else if (path.endsWith("/alerts/rules")) {
      if (route.request().method() === "POST") rule = { ...route.request().postDataJSON(), id: "local-rule", revision: 1, can_edit: true };
      data = route.request().method() === "POST" ? rule : { items: rule ? [rule] : [] };
    } else if (path.endsWith("/acknowledge")) { acknowledged = true; data = { ok: true }; }
    else if (path.endsWith("/alerts/events")) data = { items: [{ id: "local-alert", name: "队列等待", status: "FIRING",
      value: 35, first_seen_at: sample.sampled_at, last_seen_at: sample.sampled_at,
      acknowledged_at: acknowledged ? sample.sampled_at : null, recovered_at: null, can_ack: true }], next_cursor: null };
    await route.fulfill({ json: data });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "监测大盘", exact: true }).click();
  const panel = page.getByRole("region", { name: "系统监测大盘", exact: true });
  await expect(panel.getByText("采集正常", { exact: false })).toBeVisible();
  await expect(panel.getByText("66.7%", { exact: true })).toBeVisible();
  await expect(panel.getByText("进程存活：是", { exact: true })).toBeVisible();
  await expect(panel.getByText("准入就绪：否", { exact: true })).toBeVisible();
  await expect(panel.getByRole("list", { name: "运行趋势桶" }).getByRole("listitem")).toHaveCount(1);
  await panel.getByRole("button", { name: "查看此工作流运行节点" }).click();
  await expect(panel.getByText("没有节点事件，运行可能在启动前失败。")).toBeVisible();
  await expect(panel.getByText("仅显示系统状态；无权查看此运行正文或工具输出。")).toBeVisible();
  await panel.getByLabel("统计时间范围").selectOption("24");
  await expect(panel.getByText("采集正常", { exact: false })).toBeVisible();
  await expect(panel.getByText("2,400 / 240", { exact: true })).toBeVisible();
  await expect(panel.getByText("CNY 24", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "确认告警", exact: true }).click();
  await expect(panel.getByText("已确认 · 仍在告警", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "新增告警规则", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "新增告警规则", exact: true });
  await dialog.getByLabel("规则名称").fill("后台失联规则");
  await dialog.getByRole("button", { name: "保存规则", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await panel.getByRole("tab", { name: "规则列表" }).click();
  await expect(panel.getByText("后台失联规则", { exact: true })).toBeVisible();
  expect(rule).toMatchObject({ metric: "service_offline", target: "api", threshold: 1 });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test("missing or stale monitoring never claims healthy and failed reads offer retry", async ({ page }) => {
  let failed = false;
  const data = { ...snapshot(), sampled_at: null, snapshot: null, stale: true };
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { username: "自验", is_admin: true, permissions: [] } });
    if (path.endsWith("/monitoring")) {
      if (failed) return route.fulfill({ status: 503, json: { error: { message: "采集来源暂不可用" } } });
      return route.fulfill({ json: data });
    }
    return route.fulfill({ json: { items: [] } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "监测大盘", exact: true }).click();
  const panel = page.getByRole("region", { name: "系统监测大盘", exact: true });
  await expect(panel.getByText("采集过期或尚未采集", { exact: false })).toBeVisible();
  await expect(panel.getByText("采集正常", { exact: false })).toHaveCount(0);
  failed = true;
  await panel.getByRole("button", { name: "刷新大盘", exact: true }).click();
  await expect(panel.getByRole("alert").filter({ hasText: "采集来源暂不可用" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "重试采集结果", exact: true })).toBeVisible();
});
