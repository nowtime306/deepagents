import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

const message = (text: string, role = "assistant") => ({ id: randomUUID(), run_id: randomUUID(), role, content: { text } });
type History = { items: ReturnType<typeof message>[]; saved_draft?: string; next_before?: string; latest_run?: { id: string; status: string } | null };

async function mockChat(page: Page, conversations: { id: string; title: string }[], history: (id: string, before: boolean) => Promise<History> | History) {
  let sends = 0;
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url()), path = url.pathname.replace("/api/v1", "");
    let data: unknown = { items: [] };
    if (path === "/auth/me") data = { username: "流畅度验收", is_admin: true };
    if (path === "/models") data = { items: [{ id: "local-unused", name: "本机测试模型" }] };
    if (path === "/conversations") data = { items: conversations };
    const match = path.match(/^\/conversations\/([^/]+)\/messages$/);
    if (match) data = await history(match[1], url.searchParams.has("before"));
    if (path.endsWith("/usage")) data = { total: { calls: 0 }, runs: [] };
    if (path.endsWith("/references")) data = { revision: 0, items: [] };
    if (path === "/usage/estimate") data = { input_estimate: 10, output_reserve: 100 };
    if (path.endsWith("/runs") && route.request().method() === "POST") {
      sends++;
      return route.fulfill({ status: 409, json: { error: { message: "已记录本机发送，不调用外部模型" } } });
    }
    await route.fulfill({ json: data });
  });
  return () => sends;
}

test("switching conversations clears stale drafts, blocks loading sends and rejects late history", async ({ page }) => {
  const a = randomUUID(), b = randomUUID(), c = randomUUID();
  let release!: () => void;
  const delayed = new Promise<void>((done) => { release = done; });
  const sends = await mockChat(page, [{ id: a, title: "会话甲" }, { id: b, title: "会话乙" }, { id: c, title: "会话丙" }], async (id) => {
    if (id === b) await delayed;
    return { items: [message(`历史${id === a ? "甲" : id === b ? "乙" : "丙"}`)], saved_draft: id === a ? "甲的未发送草稿" : "" };
  });
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "会话甲", exact: true }).click();
    const input = page.getByRole("textbox", { name: "发送消息", exact: true });
    await expect(input).toHaveValue("甲的未发送草稿");
    await page.getByRole("button", { name: "会话乙", exact: true }).click();
    await expect(input).toHaveValue("");
    await expect(input).toBeDisabled();
    await expect(page.getByRole("status").filter({ hasText: "正在加载对话" })).toBeVisible();
    await expect(page.getByRole("button", { name: "发送", exact: true })).toBeDisabled();
    await expect(page.getByText("今天，我们一起做点什么？", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "会话丙", exact: true }).click();
    await expect(page.locator(".message-column")).toContainText("历史丙");
    release();
    await expect(input).toBeEnabled();
    await expect(input).toBeFocused();
    await expect(page.locator(".message-column")).not.toContainText("历史乙");
    expect(sends()).toBe(0);
    await input.fill("不要带进新对话");
    await page.getByRole("button", { name: "新建对话", exact: true }).click();
    await expect(input).toHaveValue("");
    await expect(input).toBeFocused();
  } finally { release(); }
});

test("composer grows and shrinks, Shift Enter inserts a line and IME confirmation never sends", async ({ page }) => {
  const id = randomUUID();
  const sends = await mockChat(page, [{ id, title: "键盘验收" }], () => ({ items: [message("键盘测试回答")] }));
  await page.goto("/");
  await page.getByRole("button", { name: "键盘验收", exact: true }).click();
  await expect(page.locator(".message-column")).toContainText("键盘测试回答");
  const input = page.getByRole("textbox", { name: "发送消息", exact: true });
  await input.fill("中文确认");
  await input.dispatchEvent("keydown", { key: "Enter", code: "Enter", keyCode: 229 });
  await input.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true });
  await input.press("Shift+Enter");
  await expect(input).toHaveValue("中文确认\n");
  expect(sends()).toBe(0);
  const shortHeight = (await input.boundingBox())!.height;
  await input.fill(Array(24).fill("多行输入内容").join("\n"));
  await expect.poll(async () => (await input.boundingBox())!.height).toBeGreaterThan(shortHeight);
  expect((await input.boundingBox())!.height).toBeLessThanOrEqual(180);
  await input.fill("正常发送");
  await expect.poll(async () => (await input.boundingBox())!.height).toBe(shortHeight);
  await input.press("Enter");
  await expect.poll(sends).toBe(1);
  await expect(input).toHaveValue("正常发送"); // A failed request preserves the unsent message.
});

test("reading older messages pauses streaming scroll and latest button resumes following", async ({ page }) => {
  const id = randomUUID(), run = randomUUID();
  const items = Array.from({ length: 28 }, (_, index) => message(`历史段落 ${index}：${"用于检查阅读位置的文字。".repeat(10)}`));
  items.push({ ...message("开始流式回答", "user"), run_id: run });
  await mockChat(page, [{ id, title: "滚动验收" }], () => ({ items, latest_run: { id: run, status: "RUNNING" } }));
  await page.addInitScript((runId) => {
    const original = window.fetch;
    const target = window as unknown as { emitChatChunk: (text: string) => void };
    let cursor = 0;
    window.fetch = (input, init) => {
      if (String(input).includes(`/runs/${runId}/events`)) return Promise.resolve(new Response(new ReadableStream({ start(controller) {
        target.emitChatChunk = (text) => controller.enqueue(new TextEncoder().encode(`id: ${++cursor}\nevent: message.delta\ndata: ${JSON.stringify({ event_id: cursor, event: "message.delta", data: { text } })}\n\n`));
      } }), { headers: { "Content-Type": "text/event-stream" } }));
      return original(input, init);
    };
  }, run);
  await page.goto("/");
  await page.getByRole("button", { name: "滚动验收", exact: true }).click();
  await page.waitForFunction(() => typeof (window as unknown as { emitChatChunk?: unknown }).emitChatChunk === "function");
  const scroll = page.locator(".chat-scroll");
  const gap = () => scroll.evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop);
  await expect.poll(gap).toBeLessThan(4);
  await scroll.hover();
  await page.mouse.wheel(0, -950);
  await expect.poll(gap).toBeGreaterThan(300);
  const position = await scroll.evaluate((element) => element.scrollTop);
  await page.evaluate(() => (window as unknown as { emitChatChunk: (text: string) => void }).emitChatChunk("\n\n" + "流式段落\n\n".repeat(100)));
  await expect(page.locator(".chat-stream")).toContainText("流式段落");
  await expect.poll(async () => Math.abs(await scroll.evaluate((element) => element.scrollTop) - position)).toBeLessThan(3);
  await expect(page.getByRole("button", { name: "回到最新消息", exact: true })).toBeVisible();
  const chunks = Array.from({ length: 200 }, (_, index) => `[片段${index}]`);
  await page.evaluate((parts) => {
    for (const part of parts) (window as unknown as { emitChatChunk: (text: string) => void }).emitChatChunk(part);
  }, chunks);
  await expect(page.locator(".chat-stream")).toContainText(chunks.join(""));
  await expect.poll(async () => Math.abs(await scroll.evaluate((element) => element.scrollTop) - position)).toBeLessThan(3);
  const input = page.getByRole("textbox", { name: "发送消息", exact: true });
  await input.fill("可以提前编辑下一条消息");
  await expect(input).toHaveValue("可以提前编辑下一条消息");
  await page.getByRole("button", { name: "回到最新消息", exact: true }).click();
  await expect.poll(gap).toBeLessThan(4);
  await page.evaluate(() => (window as unknown as { emitChatChunk: (text: string) => void }).emitChatChunk("\n\n后续内容\n\n".repeat(30)));
  await expect(page.locator(".chat-stream")).toContainText("后续内容");
  await expect.poll(gap).toBeLessThan(4);
  await page.screenshot({ path: "../../.local/chat-fluidity-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(gap).toBeLessThan(4);
  await page.screenshot({ path: "../../.local/chat-fluidity-mobile.png" });
  await page.evaluate(() => {
    (window as unknown as { emitChatChunk: (text: string) => void }).emitChatChunk("离开时尚未绘制的片段");
    (document.querySelector('button[aria-label="新建对话"]') as HTMLButtonElement).click();
  });
  await expect(page.getByText("今天，我们一起做点什么？", { exact: true })).toBeVisible();
  await expect(page.locator(".chat-stream")).toHaveCount(0);
  await expect(input).toHaveValue("");
});

test("prepending older history preserves the visible reading anchor", async ({ page }) => {
  const id = randomUUID();
  const items = Array.from({ length: 14 }, (_, index) => message(`当前段落 ${index}。${"历史阅读文字。".repeat(14)}`));
  const earlier = Array.from({ length: 10 }, (_, index) => message(`更早段落 ${index}。${"更早阅读文字。".repeat(14)}`));
  await mockChat(page, [{ id, title: "历史分页验收" }], (_, before) => ({ items: before ? earlier : items, ...(!before ? { next_before: "cursor" } : {}) }));
  await page.goto("/");
  await page.getByRole("button", { name: "历史分页验收", exact: true }).click();
  await expect(page.locator(".platform-message")).toHaveCount(14);
  await page.locator(".chat-scroll").evaluate((element) => { element.scrollTop = 0; });
  const anchor = page.locator(".platform-message").filter({ hasText: "当前段落 0。" });
  const top = (await anchor.boundingBox())!.y;
  await page.getByRole("button", { name: "加载更早消息", exact: true }).click();
  await expect(page.locator(".platform-message")).toHaveCount(24);
  await expect.poll(async () => Math.abs((await anchor.boundingBox())!.y - top)).toBeLessThan(3);
});

test("expanding a long tool process keeps the clicked summary in view", async ({ page }) => {
  const id = randomUUID(), run = randomUUID();
  const items = [message("检查过程", "user"), message("过程说明\n\n".repeat(100)), message("工具结果", "tool"), message("最终回答")]
    .map((item) => ({ ...item, run_id: run }));
  await mockChat(page, [{ id, title: "展开过程验收" }], () => ({ items }));
  await page.goto("/");
  await page.getByRole("button", { name: "展开过程验收", exact: true }).click();
  const summary = page.locator(".chat-process > summary");
  await expect(summary).toBeVisible();
  const top = (await summary.boundingBox())!.y;
  await summary.click();
  await expect(page.locator(".chat-process")).toHaveJSProperty("open", true);
  await expect.poll(async () => Math.abs((await summary.boundingBox())!.y - top)).toBeLessThan(3);
  await expect(page.getByRole("button", { name: "回到最新消息", exact: true })).toBeVisible();
});
