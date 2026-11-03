import { expect, test, type Page } from "@playwright/test";
test.use({ baseURL: "http://127.0.0.1:3101", trace: "off", video: "off", screenshot: "off" });

async function setup(page: Page) {
  const state = { cancelled: false, calls: [] as string[], payload: {} as Record<string, unknown> };
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname.replace("/api/v1", "");
    state.calls.push(`${req.method()} ${path}${url.search}`);
    let data: unknown = { items: [], summary: [], next_cursor: null };
    if (path === "/auth/me") data = { id: "qa-media", username: "媒体独立QA", is_admin: true };
    if (path === "/models") data = { items: [] };
    if (path === "/operations/services") data = { services: [], events: [], note: "本机UI替身" };
    if (path === "/operations/backups" || path === "/operations/capacity")
      return route.fulfill({ status: 503, json: { error: { message: "本用例不启用备份/容量真实服务" } } });
    if (path === "/capabilities/bindings") data = { knowledge_ids: [], skill_ids: [], mcp_ids: [] };
    if (path === "/capabilities/knowledge") data = { items: [{ id: "qa-kb", name: "媒体隔离样例", documents: 0 }] };
    if (path.endsWith("/image-index")) data = { index: { model_id: "mm", model_name: "图片向量", dimensions: 2 } };
    if (path.endsWith("/image-query/search")) data = { items: [{ id: "img", name: "已知近邻.png", score: .987 }] };
    if (path.endsWith("/videos")) data = { items: [{ id: "qa-video", name: "未知任务.mp4", status: "UNKNOWN",
      indexed_frames: 0, cancel_requested: state.cancelled, error_code: "CREATE_UNKNOWN" }], next_cursor: null };
    if (path.endsWith("/qa-video/cancel")) { state.cancelled = true; data = { cancelled: true }; }
    const base = "/operations/extra-costs";
    if (path === base+"/models") data = { items: [{ id: "image-mm", name: "图片向量模型" }, { id: "speech-mm", name: "语音合成模型" }] };
    if (path === base+"/prices") {
      if (req.method() === "POST") { state.payload = req.postDataJSON(); data = { id: "new" }; }
      else data = { items: [{ id: url.search ? "older-price" : "first-price", model_id: "image-mm", item: url.search ? "older-fee" : "image-fee", unit_price: "1", currency: "CNY", unit: "image" }], next_cursor: url.search ? null : "qa-price-cursor" };
    }
    if (path === base+"/bills") data = { items: [{ id: url.search ? "older-bill" : "first-bill", name: url.search ? "旧账单" : "首份账单" }], next_cursor: url.search ? null : "qa-bill-cursor" };
    if (path.endsWith("/bills/parse")) {
      const mapped = req.postData()?.includes('name="mapping"');
      data = { columns: ["external", "fee", "ccy", "cost"], needs_mapping: !mapped,
        rows: mapped ? [{ call_id: "00000000-0000-4000-8000-000000000001", item: "image", currency: "CNY", amount: "1" }] : [] };
    }
    if (path.endsWith("/bills/preview")) return route.fulfill({ status: 422, json: { error: { code: "BILL_SCOPE_MISMATCH", message: "调用不在声明模型或账期范围内" } } });
    await route.fulfill({ json: data });
  });
  return state;
}

test("PE-TC-090/102 image upload uses pixels and UNKNOWN cancel waits for cleanup", async ({ page }) => {
  const state = await setup(page); await page.goto("/");
  await page.getByRole("button", { name: "能力中心", exact: true }).click();
  await page.getByRole("button", { name: "管理文档与检索", exact: true }).click();
  await page.getByRole("tab", { name: "图片资料", exact: true }).click();
  const query = page.getByRole("region", { name: "图片相似检索", exact: true });
  await query.getByLabel("查询图片").setInputFiles({ name: "query.png", mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7XkAAAAASUVORK5CYII=", "base64") });
  await expect(query.getByRole("img", { name: "查询图片预览" })).toBeVisible();
  await query.getByRole("button", { name: "检索相似图片", exact: true }).click();
  await expect(query.getByRole("link", { name: "已知近邻.png" })).toBeVisible();
  await query.getByRole("button", { name: "移除图片", exact: true }).click();
  await expect(query.getByRole("img")).toHaveCount(0);
  const video = page.getByRole("region", { name: "视频帧资料", exact: true });
  await expect(video.getByText("未知任务.mp4", { exact: true })).toBeVisible();
  await video.getByRole("button", { name: "取消处理", exact: true }).click();
  await expect(video.getByRole("button", { name: "等待确认停止", exact: true })).toBeDisabled();
  await expect(video.getByRole("button", { name: "删除视频及帧", exact: true })).toHaveCount(0);
  expect(state.calls).toContain("POST /capabilities/knowledge/qa-kb/videos/qa-video/cancel");
});

test("PE-TC-120 media model dropdown and both price/bill pages remain reachable", async ({ page }) => {
  const state = await setup(page); await page.goto("/");
  await page.getByRole("button", { name: "运营管理", exact: true }).click();
  const panel = page.getByRole("region", { name: "额外费用与账单核对", exact: true });
  await panel.getByRole("button", { name: "新增计费版本", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "新增额外计费版本", exact: true });
  await dialog.getByRole("combobox", { name: "模型", exact: true }).selectOption("speech-mm");
  await dialog.getByLabel("计费项标识").fill("audio");
  await dialog.getByRole("combobox", { name: "单位", exact: true }).selectOption("audio_second");
  await dialog.getByLabel("价格来源").fill("本机样例价格");
  await dialog.getByRole("button", { name: "保存价格版本", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(state.payload.model_id).toBe("speech-mm"); expect(state.payload.unit).toBe("audio_second");
  await panel.getByRole("button", { name: "加载更早价格版本" }).click();
  await panel.getByRole("button", { name: "加载更早核对记录" }).click();
  await expect(panel.getByText("older-fee", { exact: false })).toBeVisible();
  await expect(panel.getByText("旧账单", { exact: false })).toBeVisible();
});

test("PE-TC-123 CSV explicit mapping previews before import and rejects out of scope", async ({ page }) => {
  await setup(page); await page.goto("/");
  await page.getByRole("button", { name: "运营管理", exact: true }).click();
  const panel = page.getByRole("region", { name: "额外费用与账单核对", exact: true });
  await panel.getByLabel("上传标准CSV或JSON账单").setInputFiles({ name: "qa.csv", mimeType: "text/csv", buffer: Buffer.from("external,fee,ccy,cost\na,b,CNY,1") });
  for (const [name, value] of [["调用ID", "external"], ["计费项", "fee"], ["币种", "ccy"], ["金额", "cost"]])
    await panel.getByRole("combobox", { name, exact: true }).selectOption(value);
  await panel.getByRole("button", { name: "应用字段映射", exact: true }).click();
  await panel.getByRole("button", { name: /预览核对/ }).click();
  await expect(panel.getByRole("alert")).toContainText("调用不在声明模型或账期范围内");
  await expect(panel.getByRole("button", { name: "确认导入", exact: true })).toBeDisabled();
});
