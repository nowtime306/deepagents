import { expect, test, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { createServer } from "node:http";

const origin = "http://localhost:3100", headers = { Origin: origin };
async function login(page: Page) {
  const values = Object.fromEntries(readFileSync(resolve("../../services/api/.env.admin"), "utf8").trim().split(/\r?\n/)
    .map((line) => { const i = line.indexOf("="); return [line.slice(0, i), line.slice(i + 1)]; }));
  await page.goto(origin); await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台", exact: true }).click();
  await page.getByRole("button", { name: "工作流", exact: true }).click();
}

test("canvas creates, inserts, copies, deletes, saves and tests nodes with immutable run snapshots", async ({ page }) => {
  test.setTimeout(90000); await login(page);
  const name = `画布工作台验收-${Date.now()}`; let id = "";
  try {
    await page.getByRole("button", { name: "新建工作流", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "创建工作流", exact: true });
    await dialog.getByLabel("工作流名称", { exact: true }).fill(name);
    const created = page.waitForResponse((response) => response.url().endsWith("/api/v1/workflows") && response.request().method() === "POST");
    await dialog.getByRole("button", { name: "进入画布", exact: true }).click();
    const admitted = await created; expect(admitted.status()).toBe(201); id = (await admitted.json()).id;
    const canvas = page.getByRole("region", { name: "工作流画布编辑器", exact: true });
    await expect(canvas.locator(".react-flow__node")).toHaveCount(2);
    await canvas.getByRole("button", { name: "添加节点", exact: true }).click();
    await canvas.getByRole("region", { name: "节点库", exact: true }).getByRole("button", { name: "文本转换", exact: true }).click();
    await page.getByLabel("节点名称", { exact: true }).fill("大写整理");
    await page.getByLabel("文本转换方式", { exact: true }).selectOption("upper");
    const node = canvas.locator('.react-flow__node').filter({ hasText: "大写整理" });
    const nodeId = (await node.getAttribute("data-id"))!;
    await expect(canvas.locator(".react-flow__edge")).toHaveCount(2);
    await canvas.getByRole("button", { name: "复制节点", exact: true }).click();
    await expect(canvas.locator(".react-flow__node")).toHaveCount(4);
    await canvas.getByRole("button", { name: "删除节点", exact: true }).click();
    await expect(canvas.locator(".react-flow__node")).toHaveCount(3);
    await canvas.locator('.react-flow__node[data-id="start"]').click();
    await expect(canvas.getByRole("button", { name: "删除节点", exact: true })).toBeDisabled();
    await node.click();
    const rectangle = (await node.boundingBox())!;
    await page.mouse.move(rectangle.x + 80, rectangle.y + 22); await page.mouse.down();
    await page.mouse.move(rectangle.x + 80, rectangle.y + 95, { steps: 8 }); await page.mouse.up();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("草稿已保存");
    const saved = (await (await page.request.get(`${origin}/api/v1/workflows`)).json()).items.find((row: { id: string }) => row.id === id);
    expect(saved.definition.nodes).toHaveLength(3); expect(saved.definition.edges).toHaveLength(2);
    expect(saved.layout[nodeId].y).not.toBe(200);
    await canvas.getByRole("button", { name: "单节点测试", exact: true }).click();
    await page.getByLabel("节点测试输入", { exact: true }).fill("hello");
    await page.getByRole("button", { name: "运行单节点测试", exact: true }).click();
    const trace = page.getByRole("region", { name: "节点运行情况", exact: true });
    await expect(trace).toContainText("HELLO");
    await expect(canvas.locator('.workflow-node[data-status="SUCCEEDED"]')).toHaveCount(1);
    await expect(page.getByRole("button", { name: "发布版本", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "返回编辑", exact: true }).click();
    await page.getByRole("button", { name: "测试运行", exact: true }).click();
    await page.getByLabel("测试输入", { exact: true }).fill("world");
    const completed = page.waitForResponse((response) => response.url().endsWith(`/workflows/${id}/test-runs`));
    await page.getByRole("button", { name: "运行测试", exact: true }).click();
    const result = await completed; expect(result.status()).toBe(200); const runId = (await result.json()).id;
    await expect(trace).toContainText("WORLD");
    await expect(canvas.locator('.workflow-node[data-status="SUCCEEDED"]')).toHaveCount(3);
    await page.getByRole("button", { name: "发布版本", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("已发布版本 1");
    await page.screenshot({ path: "../../.local/workflow-studio-success.png" });
    await page.getByRole("button", { name: "返回编辑", exact: true }).click();
    await node.click(); await page.getByLabel("文本转换方式", { exact: true }).selectOption("json_get");
    await page.getByLabel("字段路径", { exact: true }).fill("missing");
    await page.getByRole("button", { name: "测试运行", exact: true }).click();
    await page.getByLabel("测试输入", { exact: true }).fill("{}");
    await page.getByRole("button", { name: "运行测试", exact: true }).click();
    await expect(canvas.locator('.workflow-node[data-status="FAILED"]')).toHaveCount(1);
    await expect(trace).toContainText("执行失败");
    await page.screenshot({ path: "../../.local/workflow-studio-failure.png" });
    await page.getByRole("button", { name: "运行历史", exact: true }).click();
    await page.locator(`[data-run-id="${runId}"]`).getByRole("button").click();
    await expect(trace).toContainText("WORLD");
    await expect(page.getByText("正在查看运行时画布快照，节点参数只读。", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "返回编辑", exact: true }).click();
    await page.reload(); await page.getByRole("button", { name: "工作流", exact: true }).click();
    await page.locator(".workflow-library-card").filter({ hasText: name }).getByRole("button", { name: "编辑", exact: true }).click();
    await expect(canvas.locator(".react-flow__node")).toHaveCount(3);
    await node.click(); await expect(page.getByLabel("文本转换方式", { exact: true })).toHaveValue("json_get");
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole("button", { name: "关闭配置面板", exact: true }).click();
    await page.screenshot({ path: "../../.local/workflow-studio-mobile.png" });
  } finally { if (id) expect((await page.request.delete(`${origin}/api/v1/workflows/${id}`, { headers })).ok()).toBe(true); }
});

test("a real local model request shows running node and live completion without replay", async ({ page }) => {
  test.setTimeout(90000); let release!: () => void, calls = 0, id = "", model = "";
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  const server = createServer(async (request, response) => {
    let raw = ""; for await (const chunk of request) raw += chunk; const body = JSON.parse(raw); calls++;
    await delayed;
    const usage = { prompt_tokens: 10, completion_tokens: 3, total_tokens: 13 };
    const message = "画布实时测试完成";
    if (body.stream) {
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      for (const value of [{ choices: [{ index: 0, delta: { content: message }, finish_reason: null }] },
        { choices: [{ index: 0, delta: {}, finish_reason: "stop" }] }, { choices: [], usage }])
        response.write("data: " + JSON.stringify({ id: "local-studio", object: "chat.completion.chunk", model: body.model, created: 1, ...value }) + "\n\n");
      response.end("data: [DONE]\n\n");
    } else { response.writeHead(200, { "Content-Type": "application/json" }); response.end(JSON.stringify({ id: "local-studio", object: "chat.completion", model: body.model, created: 1,
      choices: [{ index: 0, finish_reason: "stop", message: { role: "assistant", content: message } }], usage })); }
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    await login(page); const name = `画布实时验收-${Date.now()}`;
    const createdModel = await page.request.post(`${origin}/api/v1/admin/models`, { headers, data: { name, model: "local-studio-only", base_url: `http://127.0.0.1:${(server.address() as { port: number }).port}/v1` } });
    expect(createdModel.status()).toBe(201); model = (await createdModel.json()).id;
    const workflow = await page.request.post(`${origin}/api/v1/workflows`, { headers, data: { name, model_id: model } });
    expect(workflow.status()).toBe(201); id = (await workflow.json()).id;
    await page.reload(); await page.getByRole("button", { name: "工作流", exact: true }).click();
    await page.locator(".workflow-library-card").filter({ hasText: name }).getByRole("button", { name: "测试或运行", exact: true }).click();
    await page.getByLabel("测试输入", { exact: true }).fill("本机替身验证，不调用外部模型");
    await page.getByRole("button", { name: "运行测试", exact: true }).click();
    await expect.poll(() => calls).toBe(1);
    await expect(page.locator('.react-flow__node[data-id="llm"] .workflow-node')).toHaveAttribute("data-status", "RUNNING", { timeout: 15000 });
    await expect(page.getByRole("button", { name: "保存草稿", exact: true })).toBeDisabled();
    await page.screenshot({ path: "../../.local/workflow-studio-running.png" });
    release();
    await expect(page.getByRole("region", { name: "节点运行情况", exact: true })).toContainText("画布实时测试完成");
    await expect(page.locator('.workflow-node[data-status="SUCCEEDED"]')).toHaveCount(3);
    expect(calls).toBe(1);
  } finally {
    release();
    let runs: { id: string; status: string }[] = [];
    if (id) {
      await expect.poll(async () => { runs = (await (await page.request.get(`${origin}/api/v1/workflows/${id}/runs`)).json()).items; return runs.every((run) => run.status !== "RUNNING"); }, { timeout: 15000 }).toBe(true);
      await page.request.delete(`${origin}/api/v1/workflows/${id}`, { headers });
    }
    if (model) {
      writeFileSync("test-results/workflow-studio-test-ids.json", JSON.stringify({ model, runs: runs.map((run) => run.id) }));
      expect((await page.request.delete(`${origin}/api/v1/admin/models/${model}`, { headers })).ok()).toBe(true);
    }
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
