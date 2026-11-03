import { expect, test } from "@playwright/test";
test.use({ baseURL: "http://127.0.0.1:3101", trace: "off", video: "off", screenshot: "off" });
test("BUG005 selected window keeps precise currencies and ignores late old result", async ({ page }) => {
  let release = () => {};
  const first = new Promise<void>(resolve => { release = resolve; });
  let waiting = false;
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url()), path = url.pathname;
    let data: unknown = { items: [], summary: [] };
    if (path.endsWith("/auth/me")) data = { id: "qa-window", username: "窗口独立QA", is_admin: true };
    if (path.endsWith("/monitoring")) {
      const hours = Number(url.searchParams.get("hours"));
      if (hours === 1) { waiting = true; await first; }
      data = { sampled_at: new Date().toISOString(), stale: false, snapshot: { services: [], queues: [],
        host: {}, usage: [{ input_tokens: 99999, output_tokens: 9999, unknown_tokens: 0 }], costs: [], environments: [] },
        runs: { succeeded: 0, failed: 0, p95_seconds: null, duration_samples: 0 },
        window: { from: "2026-11-01T00:00:00Z", to: "2026-11-02T00:00:00Z",
          usage: { calls: 3, input_tokens: hours * 100, output_tokens: hours * 10, unknown_tokens: 1 }, unknown_cost_calls: 1,
          costs: [{ currency: "CNY", estimated_cost: "0.000000001", unknown_calls: 1 },
            { currency: "USD", estimated_cost: "1234567890123.123456789", unknown_calls: 0 }] } };
    }
    await route.fulfill({ json: data });
  });
  await page.goto("/"); await page.getByRole("button", { name: "监测大盘", exact: true }).click();
  await expect.poll(() => waiting).toBeTruthy();
  const panel = page.getByRole("region", { name: "系统监测大盘", exact: true });
  await panel.getByRole("combobox", { name: "统计时间范围" }).selectOption("24");
  await expect(panel.getByText("2,400 / 240", { exact: true })).toBeVisible();
  release();
  await expect(panel.getByText("CNY 0.000000001；USD 1234567890123.123456789", { exact: true })).toBeVisible();
  await expect(panel.getByText("2,400 / 240", { exact: true })).toBeVisible();
  await expect(panel.getByText("费用与Token累计快照", { exact: true })).toBeVisible();
  await panel.getByRole("combobox", { name: "统计时间范围" }).selectOption("168");
  await expect(panel.getByText("16,800 / 1,680", { exact: true })).toBeVisible();
  await expect(panel.getByText("未知或未计价1次", { exact: false })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel.getByText("CNY 0.000000001；USD 1234567890123.123456789", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
});
