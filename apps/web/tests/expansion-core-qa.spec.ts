import { expect, test, type Page } from "@playwright/test";

test.use({ baseURL: "http://127.0.0.1:3101", trace: "off", video: "off", screenshot: "off" });
async function setup(page: Page, readonly = false) {
  const state = { requests: [] as string[], failRule: true, stale: true };
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url()), path = url.pathname.replace("/api/v1", "");
    state.requests.push(path + url.search);
    let data: unknown = { items: [], summary: [] };
    if (path === "/auth/me") data = { id: "qa-core", username: "独立QA", is_admin: !readonly,
      permissions: readonly ? ["monitoring.read"] : undefined };
    if (path === "/models") data = { items: [] };
    if (path === "/operations/monitoring") data = { sampled_at: "2026-11-02T00:00:00Z", stale: state.stale,
      snapshot: { host: { cpu_percent: null, memory_available: null, disk_free: null }, services: [], queues: [],
        usage: [{ input_tokens: null, output_tokens: null, unknown_tokens: 1 }],
        costs: [{ currency: "CNY", estimated_cost: null, unknown_calls: 1 }], unknown_cost_calls: 1, environments: [] },
      runs: { succeeded: 0, failed: 0, duration_samples: 0, p95_seconds: null } };
    if (path.endsWith("/alerts/events")) data = { items: [{ id: "readonly-alert", name: "系统告警只读",
      status: "FIRING", can_ack: !readonly, acknowledged_at: null }], next_cursor: null };
    if (path.endsWith("/alerts/rules") && route.request().method() === "POST" && state.failRule)
      return route.fulfill({ status: 503, json: { error: { message: "规则保存失败，原内容保留" } } });
    if (path === "/admin/models/catalog") data = { kinds: [], providers: [], protocols: [] };
    if (path === "/admin/models") data = { items: ["executable", "business_sample", "configuration_only", "unsupported"].map((s, i) => ({
      id: `matrix-${i}`, name: `本机矩阵${i}`, model: "qa", base_url: "https://unused.invalid", enabled: true,
      kind: "chat", provider: "custom", protocol: "openai", connection_status: "not_tested",
      compatibility: { state: s, entry: "鉴权能力入口", verification: "工程适配范围，真实服务效果尚未联调" } })) };
    if (path === "/capabilities/bindings") data = { knowledge_ids: [], skill_ids: [], mcp_ids: [] };
    if (path === "/capabilities/knowledge") data = { items: [{ id: "qa-kb", name: "门禁独立样例", documents: 0 }] };
    if (path.endsWith("/experiments")) data = { items: [{ id: "qa-exp", status: "SUCCEEDED", dataset_snapshot: { name: "明确合成题" } }] };
    if (path.endsWith("/quality/policy")) data = { enforce_publish: true, minimum_questions: 50, recall_target: .9,
      citation_target: .95, no_answer_target: .9, revision: 1 };
    if (path.includes("/quality/reviews/")) data = { items: [], questions: [] };
    if (path.includes("/quality/check/")) data = { passed: false, reasons: ["合成题不能通过真实业务门禁"],
      questions: 50, citation_denominator: 49, no_answer_denominator: 1, reviewed_answers: 0,
      dataset_type: "synthetic", metrics: { recall_at_10: 1, citation_correctness: null },
      note: "检索命中不能代替人工引用核验" };
    await route.fulfill({ json: data });
  });
  return state;
}

test("PE-TC-011/013/024 readonly monitoring keeps unknown and removes mutation actions", async ({ page }) => {
  const state = await setup(page, true); await page.goto("/");
  await page.getByRole("button", { name: "监测大盘", exact: true }).click();
  const panel = page.getByRole("region", { name: "系统监测大盘", exact: true });
  await expect(panel.getByText("采集过期或尚未采集", { exact: false })).toBeVisible();
  await expect(panel.getByText("暂无样本", { exact: true })).toHaveCount(2);
  await expect(panel.getByText("暂无可展示的CPU历史样本")).toBeVisible();
  await expect(panel.getByRole("button", { name: "新增告警规则" })).toHaveCount(0);
  await expect(panel.getByText("系统告警只读", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "确认告警" })).toHaveCount(0);
  await panel.getByRole("combobox", { name: "统计时间范围" }).selectOption("168");
  for (const endpoint of ["/operations/monitoring", "/operations/monitoring/series", "/operations/monitoring/run-trend", "/operations/monitoring/errors"])
    await expect.poll(() => state.requests.includes(`${endpoint}?hours=168`)).toBeTruthy();
  expect(state.requests.filter(path => path.includes("/runs") || path.includes("/acknowledge"))).toHaveLength(0);
});

test("PE-TC-020 rule failed save preserves editable input", async ({ page }) => {
  await setup(page); await page.goto("/");
  await page.getByRole("button", { name: "监测大盘", exact: true }).click();
  await page.getByRole("button", { name: "新增告警规则", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "新增告警规则", exact: true });
  await dialog.getByRole("textbox", { name: "规则名称" }).fill("保留失败输入");
  await dialog.getByRole("button", { name: "保存规则", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "规则保存失败" })).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "规则名称" })).toHaveValue("保留失败输入");
});

test("PE-TC-110 four compatibility labels do not claim real service connectivity", async ({ page }) => {
  await setup(page); await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  for (const label of ["可执行", "需业务样例检测", "仅保存配置", "未支持"])
    await expect(page.getByText(`适配状态：${label}`, { exact: false })).toBeVisible();
  await expect(page.getByText("工程适配范围，真实服务效果尚未联调", { exact: false })).toHaveCount(4);
  await expect(page.getByRole("button", { name: "图片理解", exact: true })).toHaveCount(0);
});

test("PE-TC-140/141 synthetic perfect recall still fails unreviewed citation gate", async ({ page }) => {
  await setup(page); await page.goto("/");
  await page.getByRole("button", { name: "能力中心", exact: true }).click();
  await page.getByRole("button", { name: "管理文档与检索", exact: true }).click();
  await page.getByRole("tab", { name: "评测", exact: true }).click();
  const gate = page.getByRole("region", { name: "知识质量门禁", exact: true });
  await gate.getByRole("combobox", { name: "最近100次实验" }).selectOption("qa-exp");
  await gate.getByRole("button", { name: "运行门禁检查", exact: true }).click();
  await expect(gate.getByText("门禁未通过或尚未验收", { exact: true })).toBeVisible();
  await expect(gate.getByText("生成引用正确率：未完整验收", { exact: true })).toBeVisible();
  await expect(gate.getByText("合成题不能通过真实业务门禁", { exact: true })).toBeVisible();
  await expect(gate.getByText("门禁通过（按本次人工标注）", { exact: true })).toHaveCount(0);
});
