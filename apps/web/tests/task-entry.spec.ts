import { expect, test, type Page } from "@playwright/test";
import { createServer } from "node:http";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const origin = "http://localhost:3100", headers = { Origin: origin };
async function login(page: Page) {
  const credentials = Object.fromEntries(readFileSync(resolve("../../services/api/.env.admin"), "utf8").trim().split(/\r?\n/)
    .map((line) => { const index = line.indexOf("="); return [line.slice(0, index), line.slice(index + 1)]; }));
  await page.goto(origin); await page.getByLabel("账号", { exact: true }).fill(credentials.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(credentials.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("button", { name: "退出登录", exact: true })).toBeVisible();
}

test("task and agent pages show lists first and create/edit in accessible dialogs", async ({ page }) => {
  const name = `列表入口验收-${Date.now()}`; let task = "", agent = "";
  await login(page);
  try {
    await page.getByRole("button", { name: "任务工作台", exact: true }).click();
    await expect(page.getByLabel("任务标题", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "新建任务", exact: true }).click();
    await page.getByLabel("任务标题", { exact: true }).fill(name);
    await page.getByLabel("任务目标", { exact: true }).fill("验证列表与独立编辑窗口");
    const created = page.waitForResponse((r) => r.url().endsWith("/api/v1/work-tasks") && r.request().method() === "POST");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    const result = await created; expect(result.status()).toBe(201); task = (await result.json()).id;
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByLabel("搜索任务").fill("不存在的列表项目");
    await expect(page.getByText("没有匹配的任务。", { exact: true })).toBeVisible();
    await page.getByLabel("搜索任务").fill(name);
    const card = page.locator(".run-history-card").filter({ hasText: name });
    await card.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "编辑任务草稿" })).toBeVisible();
    await page.keyboard.press("Escape"); await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.screenshot({ path: "../../.local/task-list-desktop.png" });
    await page.getByRole("button", { name: "智能体管理", exact: true }).click();
    await expect(page.getByLabel("系统指令", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "新建智能体", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "创建智能体", exact: true });
    await dialog.getByLabel("名称", { exact: true }).fill(name);
    const saved = page.waitForResponse((r) => r.url().endsWith("/api/v1/agents") && r.request().method() === "POST");
    await dialog.getByRole("button", { name: "保存智能体", exact: true }).click();
    const response = await saved; expect(response.status()).toBe(201); agent = (await response.json()).id;
    await page.getByLabel("搜索智能体").fill(name);
    await expect(card).toContainText(name); await card.getByRole("button", { name: "编辑", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole("dialog", { name: "编辑智能体", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: "../../.local/agent-list-mobile-editor.png" });
    await page.getByRole("button", { name: "关闭编辑窗口", exact: true }).click();
  } finally {
    if (task) expect((await page.request.delete(`${origin}/api/v1/work-tasks/${task}`, { headers })).ok()).toBe(true);
    if (agent) expect((await page.request.delete(`${origin}/api/v1/agents/${agent}`, { headers })).ok()).toBe(true);
  }
});

test("real chat tools create one paused schedule, card enables it and the worker executes once", async ({ page }) => {
  test.setTimeout(90000); page.setDefaultTimeout(15000);
  const name = `主动任务页面验收-${Date.now()}`;
  let model = "", agent = "", published = "", schedule = "", calls = 0, automaticHasCreationTool = false;
  const conversations: string[] = [], runs: string[] = [];
  const stub = createServer(async (request, response) => {
    let raw = ""; for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw); calls++;
    const toolMessages = body.messages.filter((message: { role: string }) => message.role === "tool");
    const automatic = body.messages.some((message: { role: string; content: unknown }) => message.role === "user" && JSON.stringify(message.content).includes("本机自动任务执行验证"));
    if (automatic) automaticHasCreationTool = body.tools?.some((item: { function?: { name: string } }) => item.function?.name === "create_automation_draft");
    const invocation = automatic || toolMessages.length >= 2 ? null : {
      id: toolMessages.length ? "draft-once" : "list-targets", type: "function",
      function: { name: toolMessages.length ? "create_automation_draft" : "list_automation_agents",
        arguments: JSON.stringify(toolMessages.length ? { schedule: { name, agent_version_id: published,
          goal: "本机自动任务执行验证", starts_at: new Date(Date.now() + 4000).toISOString(), missed_policy: "once" } } : {}) },
    };
    const text = automatic ? "本机自动任务执行完成" : "自动任务草稿已保存，请启用。";
    const usage = { prompt_tokens: 9, completion_tokens: 3, total_tokens: 12 };
    const id = `task-entry-${calls}`, finish_reason = invocation ? "tool_calls" : "stop";
    if (body.stream) {
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      const send = (choices: unknown[], extra = {}) => response.write("data: " + JSON.stringify({ id, object: "chat.completion.chunk", created: 1, model: body.model, choices, ...extra }) + "\n\n");
      send([{ index: 0, delta: invocation ? { role: "assistant", tool_calls: [{ index: 0, ...invocation }] } : { role: "assistant", content: text }, finish_reason: null }]);
      send([{ index: 0, delta: {}, finish_reason }]); send([], { usage }); response.end("data: [DONE]\n\n");
    } else { response.writeHead(200, { "Content-Type": "application/json" }); response.end(JSON.stringify({ id, object: "chat.completion", created: 1, model: body.model, usage,
      choices: [{ index: 0, finish_reason, message: invocation ? { role: "assistant", content: "", tool_calls: [invocation] } : { role: "assistant", content: text } }] })); }
  });
  await new Promise<void>((done) => stub.listen(0, "127.0.0.1", done));
  try {
    await login(page);
    const created = await page.request.post(`${origin}/api/v1/admin/models`, { headers, data: { name, model: "local-task-entry", base_url: `http://127.0.0.1:${(stub.address() as { port: number }).port}/v1`, supports_tools: true } });
    expect(created.status()).toBe(201); model = (await created.json()).id;
    const configured = await page.request.post(`${origin}/api/v1/agents`, { headers, data: { name, model_id: model } });
    expect(configured.status()).toBe(201); agent = (await configured.json()).id;
    const release = await page.request.post(`${origin}/api/v1/agents/${agent}/publish`, { headers, data: { revision: 1 } });
    expect(release.status()).toBe(201); published = (await release.json()).id;
    await page.reload(); await page.getByLabel("选择模型", { exact: true }).selectOption(model);
    await page.getByLabel("发送消息", { exact: true }).fill("请使用工具创建一个自动任务草稿");
    const submitted = page.waitForResponse((r) => /\/conversations\/[^/]+\/runs$/.test(r.url()) && r.request().method() === "POST");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    const sent = await submitted; expect(sent.status()).toBe(202); const root = await sent.json(); runs.push(root.run_id);
    conversations.push(sent.url().split("/conversations/")[1].split("/")[0]);
    const card = page.getByRole("region", { name: "对话自动任务", exact: true });
    await expect(card).toContainText("草稿已保存，启用后才会执行。", { timeout: 30000 });
    const listing = (await (await page.request.get(`${origin}/api/v1/automations`)).json()).items.filter((row: { name: string }) => row.name === name);
    expect(listing).toHaveLength(1); schedule = listing[0].id; expect(listing[0].enabled).toBe(false);
    await expect.poll(() => calls).toBe(3);
    await page.reload(); await page.getByRole("button", { name: "请使用工具创建一个自动任务草稿", exact: true }).click();
    await expect(card).toContainText(name);
    await card.getByRole("button", { name: "启用自动任务", exact: true }).click();
    await expect(card).toContainText("已启用，可在自动任务列表管理。");
    await expect.poll(async () => {
      const items = (await (await page.request.get(`${origin}/api/v1/automations/${schedule}/occurrences`)).json()).items;
      return items[0]?.result_status;
    }, { timeout: 30000 }).toBe("SUCCEEDED");
    const occurrences = (await (await page.request.get(`${origin}/api/v1/automations/${schedule}/occurrences`)).json()).items;
    expect(occurrences).toHaveLength(1); runs.push(occurrences[0].ask_run_id); conversations.push(occurrences[0].target_id);
    expect(calls).toBe(4); expect(automaticHasCreationTool).toBeFalsy();
    await page.screenshot({ path: "../../.local/chat-automation-created.png" });
    await page.getByRole("button", { name: "自动任务", exact: true }).click();
    await page.getByLabel("搜索自动任务", { exact: true }).fill(name);
    await expect(page.locator(".automatic-list article").filter({ hasText: name })).toHaveCount(1);
  } finally {
    test.setTimeout(test.info().timeout + 15000);
    if (model && !schedule) { const response = await page.request.get(`${origin}/api/v1/automations`); if (response.ok()) schedule = (await response.json()).items.find((row: { name: string }) => row.name === name)?.id || ""; }
    writeFileSync("test-results/chat-automation-test-ids.json", JSON.stringify({ name, model, agent, schedule, batch: "", channel: "", conversations, runs }));
    if (schedule) await page.request.delete(`${origin}/api/v1/automations/${schedule}`, { headers });
    for (const id of conversations) await page.request.delete(`${origin}/api/v1/conversations/${id}`, { headers });
    if (agent) await page.request.delete(`${origin}/api/v1/agents/${agent}`, { headers });
    if (model) await page.request.delete(`${origin}/api/v1/admin/models/${model}`, { headers });
    stub.closeAllConnections(); await new Promise<void>((done) => stub.close(() => done()));
  }
});
