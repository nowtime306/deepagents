import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";

test("only final answers show answer actions across tool steps, older turns and reload", async ({ page }) => {
  const conversation = randomUUID(), first = randomUUID(), second = randomUUID(), interrupted = randomUUID();
  const message = (role: string, run_id: string, text: string, calls = false) => ({
    id: randomUUID(), role, run_id, content: { data: { content: text,
      ...(calls ? { tool_calls: [{ name: "web_search" }] } : {}) } },
  });
  const items = [
    message("user", first, "微博今天的热搜"),
    message("assistant", first, "我来搜索一下今天微博热搜的情况。", true),
    message("tool", first, "本机测试搜索结果"),
    message("assistant", first, "第一轮最终回答，附上已核对的来源。"),
    message("user", second, "继续核对来源"),
    message("assistant", second, "正在核对来源。"),
    message("tool", second, "旧记录未保留前一条工具声明"),
    message("assistant", second, "第二轮最终回答。"),
    message("user", interrupted, "再搜索一次"),
    message("assistant", interrupted, "我再检查一下。", true),
  ];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let data: unknown = { items: [] };
    if (path === "/auth/me") data = { username: "页面验收", is_admin: true };
    if (path === "/models") data = { items: [{ id: "local-unused", name: "本机测试模型" }] };
    if (path === "/conversations") data = { items: [{ id: conversation, title: "消息操作验收" }] };
    if (path === `/conversations/${conversation}/messages`) data = { items, latest_run: { id: interrupted, status: "FAILED" } };
    if (path === `/conversations/${conversation}/usage`) data = { total: { calls: 0 }, runs: [] };
    if (path === `/runs/${first}`) data = { status: "SUCCEEDED", created_at: "2026-11-01T00:00:00Z", finished_at: "2026-11-01T00:07:06Z" };
    if (path === `/runs/${second}`) data = { status: "SUCCEEDED", created_at: "2026-11-01T00:08:00Z", finished_at: "2026-11-01T00:08:04Z" };
    if (path === `/runs/${interrupted}`) data = { status: "FAILED", created_at: "2026-11-01T00:09:00Z", finished_at: "2026-11-01T00:09:02Z" };
    if (path.endsWith("/references")) data = { revision: 0, items: [] };
    await route.fulfill({ json: data });
  });
  for (const reload of [false, true]) {
    if (reload) await page.reload(); else await page.goto("http://localhost:3100");
    await page.getByRole("button", { name: "消息操作验收", exact: true }).click();
    const processes = page.locator(".chat-process");
    await expect(processes).toHaveCount(3);
    await expect(page.locator(`.chat-turn[data-run-id="${first}"] > .chat-process > summary`)).toContainText("用时 7分6秒");
    for (const text of ["我来搜索一下今天微博热搜的情况。", "正在核对来源。", "我再检查一下。"]) {
      await expect(page.locator(".platform-message.assistant").filter({ hasText: text })).toBeHidden();
    }
    for (const process of await processes.all()) await process.locator(":scope > summary").click();
    for (const text of ["我来搜索一下今天微博热搜的情况。", "正在核对来源。", "我再检查一下。"]) {
      const row = page.locator(".platform-message.assistant").filter({ hasText: text });
      await expect(row).toBeVisible();
      await expect(row.getByRole("button")).toHaveCount(0);
    }
    for (const text of ["第一轮最终回答，附上已核对的来源。", "第二轮最终回答。"]) {
      const row = page.locator(".platform-message.assistant").filter({ hasText: text });
      await expect(row.getByRole("button", { name: "复制回答", exact: true })).toBeVisible();
      await expect(row.getByRole("button", { name: "评价回答", exact: true })).toHaveCount(1);
      await expect(row.getByRole("button", { name: "从此消息创建分支", exact: true })).toHaveCount(1);
    }
    await expect(page.getByRole("button", { name: "复制提问", exact: true })).toHaveCount(3);
    await expect(page.getByRole("button", { name: "复制回答", exact: true })).toHaveCount(2);
  }
  await page.screenshot({ path: "../../.local/chat-message-actions.png", fullPage: true });
});

test("live tool steps update the process and late progress reads cannot replace the final answer", async ({ page }) => {
  const conversation = randomUUID(), run = randomUUID(), started = new Date().toISOString();
  const user = { id: randomUUID(), run_id: run, role: "user", content: { text: "实时布局测试" } };
  const intro = { id: randomUUID(), run_id: run, role: "assistant", content: { data: { content: "正在查询本机测试来源。", tool_calls: [{ name: "web_search" }] } } };
  const result = { id: randomUUID(), run_id: run, role: "tool", content: { data: { content: "本机测试结果", name: "web_search" } } };
  const answer = { id: randomUUID(), run_id: run, role: "assistant", content: { text: "这是最终结果。" } };
  let phase = 0, progressReads = 0;
  let finish!: () => void, releaseStale!: () => void;
  const completion = new Promise<void>((done) => { finish = done; });
  const stale = new Promise<void>((done) => { releaseStale = done; });
  const history = (state: number) => ({ items: state === 2 ? [user, intro, result, answer] : state === 1 ? [user, intro, result] : [user], latest_run: { id: run, status: state === 2 ? "SUCCEEDED" : "RUNNING" } });
  const frames = (items: { event: string; data: object }[], start: number) => items.map((item, index) => {
    const event_id = start + index;
    return `id: ${event_id}\nevent: ${item.event}\ndata: ${JSON.stringify({ ...item, event_id })}\n\n`;
  }).join("");
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url()), path = url.pathname.replace("/api/v1", "");
    let data: unknown = { items: [] };
    if (path === "/auth/me") data = { username: "实时验收", is_admin: true };
    if (path === "/models") data = { items: [{ id: "unused", name: "本机测试模型" }] };
    if (path === "/conversations") data = { items: [{ id: conversation, title: "实时布局验收" }] };
    if (path === `/conversations/${conversation}/messages`) {
      const state = phase;
      if (state === 1 && ++progressReads === 2) await stale;
      data = history(state);
    }
    if (path === `/conversations/${conversation}/usage`) data = { total: { calls: 0 }, runs: [] };
    if (path.endsWith("/references")) data = { revision: 0, items: [] };
    if (path === `/runs/${run}`) data = { status: phase === 2 ? "SUCCEEDED" : "RUNNING", claimed_at: started,
      ...(phase === 2 ? { finished_at: new Date(Date.parse(started) + 2000).toISOString() } : {}) };
    if (path === `/runs/${run}/events`) {
      if (Number(url.searchParams.get("after_event_id")) === 0) {
        phase = 1;
        return route.fulfill({ contentType: "text/event-stream", body: frames([
          { event: "message.delta", data: { text: "正在查询本机测试来源。" } },
          { event: "message.completed", data: { role: "assistant" } },
          { event: "message.completed", data: { role: "tool" } },
        ], 1) });
      }
      await completion; phase = 2;
      return route.fulfill({ contentType: "text/event-stream", body: frames([
        { event: "message.delta", data: { text: "这是最终结果。" } },
        { event: "message.completed", data: { role: "assistant" } },
        { event: "run.completed", data: {} },
      ], 4) });
    }
    await route.fulfill({ json: data });
  });
  try {
    await page.goto("http://localhost:3100");
    await page.getByRole("button", { name: "实时布局验收", exact: true }).click();
    const process = page.locator(".chat-process");
    await expect(process).toHaveJSProperty("open", true);
    await expect(process).toContainText("正在查询本机测试来源。");
    await expect(process.getByRole("button", { name: "复制回答", exact: true })).toHaveCount(0);
    await expect.poll(() => progressReads).toBe(2);
    finish();
    const final = page.locator(".chat-turn > .platform-message.assistant").filter({ hasText: "这是最终结果。" });
    await expect(final.getByRole("button", { name: "复制回答", exact: true })).toBeVisible();
    await expect(process).toHaveJSProperty("open", false);
    await expect(process.locator(":scope > summary")).toContainText("用时 2秒");
    const staleResponse = page.waitForResponse((response) => response.url().endsWith(`/conversations/${conversation}/messages`) && phase === 2);
    releaseStale(); await staleResponse;
    await expect(final).toBeVisible();
    await expect(page.getByRole("button", { name: "复制回答", exact: true })).toHaveCount(1);
    await process.locator(":scope > summary").click();
    await page.screenshot({ path: "../../.local/chat-turn-layout-expanded.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: "../../.local/chat-turn-layout-mobile.png", fullPage: true });
  } finally { finish(); releaseStale(); }
});
