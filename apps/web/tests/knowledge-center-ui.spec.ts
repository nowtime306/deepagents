import { expect, test, type Page } from "@playwright/test";

type Doc = { id: string; name: string; size: number; chunks: number; revision: number; vector_status: string; error?: string };
async function fixture(page: Page, options: { readonly?: boolean; empty?: boolean } = {}) {
  const state = {
    calls: [] as string[], listFailure: false, searchFailure: false, deleteFailure: false,
    emptySearch: false, uploadFailure: false, uploadWait: null as Promise<void> | null,
    listWait: null as Promise<void> | null, documentWait: null as Promise<void> | null,
    documents: [
      { id: "doc-a", name: "产品资料.md", size: 2048, chunks: 12, revision: 2, vector_status: "READY" },
      { id: "doc-b", name: "索引失败.txt", size: 1024, chunks: 3, revision: 1, vector_status: "FAILED", error: "本机样例索引失败" },
      { id: "doc-c", name: "关键词资料.txt", size: 512, chunks: 2, revision: 1, vector_status: "SKIPPED" },
    ] as Doc[],
  };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.replace("/api/v1", "");
    const method = request.method(); state.calls.push(`${method} ${path}`);
    let data: unknown = { items: [] };
    const fail = (message: string) => route.fulfill({ status: 503, json: { error: { code: "LOCAL_TEST_ERROR", message } } });
    if (path === "/auth/me") data = { username: "知识体验验收", is_admin: !options.readonly,
      permissions: options.readonly ? ["knowledge.read"] : undefined };
    if (path === "/models") data = { items: [{ id: "unused", name: "本机模型" }] };
    if (path === "/capabilities/knowledge") {
      if (state.listWait) await state.listWait;
      if (state.listFailure) return fail("知识列表读取失败，请重试");
      data = { items: options.empty ? [] : [
        { id: "kb-a", name: "产品手册", description: "售后规则与设备使用说明", documents: state.documents.length,
          embedding_model_id: null, rerank_model_id: null, keyword_min_coverage: 0 },
        { id: "kb-b", name: "团队规范", description: "研发入门和协作流程", documents: 0,
          embedding_model_id: null, rerank_model_id: null, keyword_min_coverage: 0 },
      ] };
    }
    if (path === "/capabilities/bindings") data = { knowledge_ids: [], skill_ids: [], mcp_ids: [] };
    if (path.endsWith("/documents") && method === "GET") {
      if (path.includes("/kb-a/") && state.documentWait) await state.documentWait;
      data = { items: path.includes("/kb-a/") ? state.documents : [] };
    }
    if (path.endsWith("/documents") && method === "POST") {
      if (state.uploadWait) await state.uploadWait;
      if (state.uploadFailure) return fail("本机上传请求失败，请刷新核对");
      state.documents.push({ id: "uploaded", name: "新资料.txt", size: 20, chunks: 1, revision: 1, vector_status: "SKIPPED" });
      data = { id: "uploaded", duplicate: false };
    }
    if (path.includes("/documents/") && method === "DELETE") {
      if (state.deleteFailure) return fail("删除失败，文档仍保留");
      state.documents = state.documents.filter((document) => !path.endsWith("/" + document.id));
      data = { deleted: true };
    }
    if (path.endsWith("/search") && method === "POST") {
      if (state.searchFailure) return fail("本次检索失败，请检查配置后重试");
      data = { mode: "keyword", reranked: false, evidence_status: state.emptySearch ? "no_evidence" : "candidates",
        items: state.emptySearch ? [] : [{ chunk_id: "chunk-1", name: "产品资料.md", location: "第2节", text: "产品保修三年，凭发票申请维修。", score: 1 }] };
    }
    if (path.endsWith("/processing")) data = { chunk_size: 1200, chunk_overlap: 200, revision: 1 };
    if (path.endsWith("/image-index")) data = { index: { model_id: "local-image-unused", model_name: "本机图片索引样例", dimensions: 2 } };
    if (path.endsWith("/revisions")) data = { items: [{ revision: 2, name: "产品资料.md", chunks: 12, chunk_size: 1200, chunk_overlap: 200, parse_method: "text", created_at: "2026-11-02T00:00:00Z" }] };
    if (path.endsWith("/chunks")) data = { items: [{ id: "chunk-1", position: 0, location: "第2节", content: "真实接口样例分块正文" }] };
    await route.fulfill({ json: data });
  });
  return state;
}
async function open(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "能力中心", exact: true }).click();
}
async function enter(page: Page, name = "产品手册") {
  await page.locator("article").filter({ has: page.getByRole("heading", { name, exact: true }) })
    .getByRole("button", { name: "管理文档与检索", exact: true }).click();
}

test("KB-01/02/09/11/13 list navigation loads advanced data on demand and hides standalone media entries", async ({ page }) => {
  const state = await fixture(page); await open(page);
  for (const name of ["语音合成", "图片理解"]) await expect(page.getByRole("button", { name, exact: true, includeHidden: true })).toHaveCount(0);
  const librarySearch = page.getByRole("textbox", { name: /搜索.*资源/ });
  await librarySearch.fill("售后");
  await expect(page.getByRole("heading", { name: "产品手册", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "团队规范", exact: true })).toHaveCount(0);
  await librarySearch.fill("不存在的资料");
  await expect(page.getByText(/没有匹配/).first()).toBeVisible();
  await librarySearch.fill(""); await enter(page);
  await expect(page.getByRole("tab", { name: "文档", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("region", { name: "知识文档列表", exact: true })).toContainText("产品资料.md");
  expect(state.calls.filter((path) => /\/(ocr|datasets|experiments|operations|images|image-index|processing)(?:$|\/)/.test(path))).toEqual([]);
  await page.getByRole("tab", { name: "检索测试", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "检索问题", exact: true })).toBeVisible();
  await expect(page.getByRole("table", { name: "知识文档表", exact: true })).toHaveCount(0);
  await page.getByRole("tab", { name: "配置", exact: true }).click();
  await expect.poll(() => state.calls.some((path) => path.endsWith("/processing"))).toBe(true);
  await page.getByRole("tab", { name: "处理记录", exact: true }).click();
  await expect.poll(() => state.calls.some((path) => path.endsWith("/operations"))).toBe(true);
  await page.getByRole("tab", { name: "评测", exact: true }).click();
  await expect.poll(() => state.calls.some((path) => path.endsWith("/datasets"))).toBe(true);
  await page.getByRole("tab", { name: "图片资料", exact: true }).click();
  await expect.poll(() => state.calls.some((path) => path.endsWith("/images"))).toBe(true);
  await page.getByRole("tab", { name: "文档", exact: true }).click();
  await page.getByRole("button", { name: "OCR导入", exact: true }).click();
  await expect.poll(() => state.calls.some((path) => path.endsWith("/ocr"))).toBe(true);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "展开侧边栏", exact: true }).click();
  for (const name of ["语音合成", "图片理解"]) await expect(page.getByRole("button", { name, exact: true, includeHidden: true })).toHaveCount(0);
  await page.getByRole("button", { name: "能力中心", exact: true }).click();
  for (const name of ["文档", "检索测试", "配置", "处理记录", "评测", "图片资料"]) await expect(page.getByRole("tab", { name, exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "图片资料", exact: true }).click();
  await expect(page.getByLabel("图片检索问题", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "文档", exact: true }).click();
  await page.getByRole("button", { name: "OCR导入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "扫描件与图片导入", exact: true })).toBeVisible();
});

test("KB-03/05/06 file filtering, preview and deletion preserve context and recover after failure", async ({ page }) => {
  const state = await fixture(page); await open(page); await enter(page);
  const filter = page.getByRole("combobox", { name: "文档状态筛选", exact: true });
  await filter.selectOption("warning");
  const table = page.getByRole("table", { name: "知识文档表", exact: true });
  await expect(table).toContainText("索引失败.txt"); await expect(table).not.toContainText("产品资料.md");
  await filter.selectOption("all");
  const search = page.getByRole("textbox", { name: "搜索文档", exact: true });
  await search.fill("产品");
  const row = table.getByRole("row").filter({ hasText: "产品资料.md" });
  await row.getByRole("button", { name: "分块与修订历史", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("真实接口样例分块正文");
  await page.keyboard.press("Escape"); await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(search).toHaveValue("产品");
  page.once("dialog", (dialog) => dialog.dismiss()); await row.getByRole("button", { name: "删除", exact: true }).click();
  expect(state.calls.filter((call) => call.startsWith("DELETE "))).toHaveLength(0);
  state.deleteFailure = true;
  page.once("dialog", (dialog) => { expect(dialog.message()).toContain("产品资料.md"); return dialog.accept(); });
  await row.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "删除失败" })).toBeVisible(); await expect(row).toBeVisible();
  state.deleteFailure = false;
  page.once("dialog", (dialog) => dialog.accept()); await row.getByRole("button", { name: "删除", exact: true }).click();
  await expect(row).toHaveCount(0);
});

test("KB-04/12 upload validates before requesting, blocks duplicates and refreshes after success", async ({ page }) => {
  const state = await fixture(page); await open(page); await enter(page);
  await page.getByRole("button", { name: "上传文档", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "上传文档", exact: true });
  await expect(dialog).toContainText("4 MB");
  const file = dialog.getByLabel("上传知识文档", { exact: true });
  await file.setInputFiles({ name: "bad.exe", mimeType: "application/octet-stream", buffer: Buffer.from("invalid") });
  await expect(dialog.getByRole("alert")).toBeVisible();
  expect(state.calls.filter((call) => call.startsWith("POST ") && call.endsWith("/documents"))).toHaveLength(0);
  await file.setInputFiles({ name: "oversized.txt", mimeType: "text/plain", buffer: Buffer.alloc(4 * 1024 * 1024 + 1) });
  await expect(dialog.getByRole("alert")).toBeVisible();
  expect(state.calls.filter((call) => call.startsWith("POST ") && call.endsWith("/documents"))).toHaveLength(0);
  let release!: () => void;
  state.uploadWait = new Promise<void>((done) => { release = done; });
  try {
    await file.setInputFiles({ name: "新资料.txt", mimeType: "text/plain", buffer: Buffer.from("新资料正文") });
    await dialog.getByRole("button", { name: "开始上传", exact: true }).click();
    await expect(dialog).toContainText("正在上传并处理");
    await expect(dialog.getByRole("button", { name: /开始上传|正在上传并处理/ })).toBeDisabled();
    await expect.poll(() => state.calls.filter((call) => call.startsWith("POST ") && call.endsWith("/documents")).length).toBe(1);
    release(); await expect(dialog).toContainText(/成功|已上传|已导入/);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("region", { name: "知识文档列表", exact: true })).toContainText("新资料.txt");
  } finally { release(); }
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.getByRole("tab", { name: "检索测试", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "检索问题", exact: true })).toBeVisible();
  await page.screenshot({ path: "../../.local/knowledge-center-mobile.png" });
});

test("KB-07 retrieval has explicit sources, clears stale results on error and explains empty evidence", async ({ page }) => {
  const state = await fixture(page); await open(page); await enter(page);
  await page.getByRole("tab", { name: "检索测试", exact: true }).click();
  const input = page.getByRole("textbox", { name: "检索问题", exact: true });
  await input.fill("产品保修"); await page.getByRole("button", { name: "检索测试", exact: true }).click();
  await expect(page.getByText("产品保修三年，凭发票申请维修。", { exact: true })).toBeVisible();
  await expect(page.locator(".knowledge-results")).toContainText("产品资料.md");
  state.searchFailure = true;
  await input.fill("失败请求"); await page.getByRole("button", { name: "检索测试", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "本次检索失败" })).toBeVisible();
  await expect(page.getByText("产品保修三年，凭发票申请维修。", { exact: true })).toHaveCount(0);
  state.searchFailure = false; state.emptySearch = true;
  await input.fill("没有的内容"); await page.getByRole("button", { name: "检索测试", exact: true }).click();
  await expect(page.locator(".knowledge-results")).toContainText(/未找到|没有找到|无匹配/);
  await page.getByRole("button", { name: "返回知识库", exact: true }).click(); await enter(page, "团队规范");
  await expect(page.getByRole("region", { name: "知识文档列表", exact: true })).not.toContainText("产品资料.md");
  await page.getByRole("tab", { name: "检索测试", exact: true }).click(); await expect(input).toHaveValue("");
});

test("KB-10 read-only knowledge users can browse without management controls", async ({ page }) => {
  const state = await fixture(page, { readonly: true }); await open(page);
  await expect(page.getByRole("button", { name: "新建知识库", exact: true })).toHaveCount(0);
  await enter(page);
  await expect(page.getByRole("region", { name: "知识文档列表", exact: true })).toContainText("产品资料.md");
  await expect(page.getByRole("button", { name: "上传文档", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "删除", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("更新此文档", { exact: true })).toHaveCount(0);
  await page.getByRole("tab", { name: "配置", exact: true }).click();
  await expect(page.getByRole("button", { name: /保存/ })).toHaveCount(0);
  expect(state.calls.filter((call) => /\/capabilities\/(mcp|skills|bindings)$/.test(call))).toEqual([]);
});

test("KB-01/11 list errors offer retry instead of a false empty library", async ({ page }) => {
  const state = await fixture(page); state.listFailure = true; await open(page);
  await expect(page.getByRole("alert").filter({ hasText: "知识列表读取失败" })).toBeVisible();
  await expect(page.getByText("还没有知识库", { exact: true })).toHaveCount(0);
  state.listFailure = false;
  await page.getByRole("button", { name: /重试|重新加载/ }).click();
  await expect(page.getByRole("heading", { name: "产品手册", exact: true })).toBeVisible();
});

test("KB-01/11 an initial pending request is not shown as a successfully empty library", async ({ page }) => {
  const state = await fixture(page, { empty: true });
  let release!: () => void;
  state.listWait = new Promise<void>((done) => { release = done; });
  try {
    await open(page);
    await expect.poll(() => state.calls.includes("GET /capabilities/knowledge")).toBe(true);
    await expect(page.getByText("还没有知识库", { exact: true })).toHaveCount(0);
    release();
    await expect(page.getByText("还没有知识库", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "新建知识库", exact: true })).toBeVisible();
  } finally { release(); }
});

test("KB-02 late document reads from the previous library never replace the current library", async ({ page }) => {
  const state = await fixture(page);
  let settled = false;
  const onSettled = (request: { url: () => string }) => {
    if (request.url().endsWith("/kb-a/documents")) settled = true;
  };
  page.on("requestfinished", onSettled); page.on("requestfailed", onSettled);
  let release!: () => void;
  state.documentWait = new Promise<void>((done) => { release = done; });
  try {
    await open(page); await enter(page);
    await expect.poll(() => state.calls.includes("GET /capabilities/knowledge/kb-a/documents")).toBe(true);
    await page.getByRole("button", { name: "返回知识库", exact: true }).click();
    await enter(page, "团队规范");
    const region = page.getByRole("region", { name: "知识文档列表", exact: true });
    await expect(region).toContainText(/上传|还没有/);
    release(); await expect.poll(() => settled).toBe(true);
    await expect(region).not.toContainText("产品资料.md");
    await expect(page.locator(".knowledge-workspace")).toContainText("团队规范");
  } finally { release(); }
});
