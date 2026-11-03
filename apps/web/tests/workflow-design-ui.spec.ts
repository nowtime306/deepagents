import { expect, test, type Page } from "@playwright/test";
import type { WorkflowDefinition } from "../src/features/ask/workflow-editor";

test("template creation, grouped search, undo/redo and check-to-node navigation", async ({ page }) => {
  let item: { id: string; name: string; model_id: string; instructions: string; revision: number; definition: WorkflowDefinition; layout: object } | null = null;
  const requests: string[] = [];
  await page.route("**/api/v1/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace("/api/v1", "");
    requests.push(path);
    let data: unknown = { items: [] }, status = 200;
    if (path === "/auth/me") data = { username: "本机组件验证", is_admin: true };
    if (path === "/models") data = { items: [{ id: "unused", name: "未调用的测试模型" }] };
    if (path === "/workflows" && request.method() === "POST") { item = { ...request.postDataJSON(), id: "flow-1", revision: 1 }; data = { id: item!.id }; status = 201; }
    else if (path === "/workflows") data = { items: item ? [item] : [] };
    if (path === "/workflows/flow-1" && request.method() === "PUT") { const saved = { ...item!, ...request.postDataJSON(), revision: item!.revision + 1 }; item = saved; data = { id: saved.id }; }
    if (path === "/workflows/validate") {
      const definition: WorkflowDefinition = request.postDataJSON().definition;
      const invalid = definition.nodes.find(node => node.parameters.template === "{{missing}}");
      data = { valid: !invalid, scope: "configuration", issues: invalid ? [{ code: "VARIABLE_UNAVAILABLE", node_id: invalid.id, field: "parameters.template", message: "引用了尚不可用的变量 missing" }] : [] };
    }
    await route.fulfill({ status, json: data });
  });
  await page.goto("/"); await page.getByRole("button", { name: "工作流", exact: true }).click();
  await page.getByRole("button", { name: "新建工作流", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "创建工作流", exact: true });
  await dialog.getByLabel("工作流名称", { exact: true }).fill("设计工具组件验证");
  await dialog.getByRole("button", { name: /模板报告/ }).click();
  await expect(dialog.getByLabel("工作流初始模板")).toHaveValue("report");
  await dialog.getByRole("button", { name: "进入画布", exact: true }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(5);
  await page.getByRole("button", { name: "添加节点", exact: true }).first().click();
  await page.getByLabel("搜索节点").fill("template");
  await page.getByRole("region", { name: "节点库" }).getByRole("button", { name: "模板组合", exact: true }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(6);
  await page.getByLabel("节点模板内容").fill("新内容 {{input}}");
  await page.getByRole("button", { name: "撤销编辑" }).click();
  await expect(page.getByLabel("节点模板内容")).toHaveValue("{{input}}");
  await page.getByRole("button", { name: "撤销编辑" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(5);
  await page.getByRole("button", { name: "重做编辑" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(6);
  await page.getByLabel("节点模板内容").fill("{{missing}}");
  await page.getByRole("button", { name: "检查流程", exact: true }).click();
  await page.getByRole("region", { name: "流程检查结果" }).getByRole("button", { name: /引用了尚不可用的变量/ }).click();
  await expect(page.getByLabel("节点模板内容")).toHaveValue("{{missing}}");
  await page.getByLabel("节点模板内容").fill("{{input}}");
  await page.getByRole("button", { name: "整理画布", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("画布已整理");
  await page.getByRole("button", { name: "检查流程", exact: true }).click();
  await expect(page.getByRole("region", { name: "流程检查结果" })).toContainText("配置检查通过");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(requests.some(path => /test-runs|work-runs/.test(path))).toBe(false);
});

async function existingFlow(page: Page, definition: WorkflowDefinition, layout: object = {}, events: object[] = []) {
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let data: unknown = { items: [] };
    if (path === "/auth/me") data = { username: "本机组件验证", is_admin: true };
    if (path === "/models") data = { items: [{ id: "unused", name: "未调用模型" }] };
    if (path === "/workflows") data = { items: [{ id: "existing", name: "恢复反例", model_id: "unused", instructions: "", revision: 1, definition, layout }] };
    if (path === "/workflows/existing/runs") data = { items: events.length ? [{ id: "previous", status: "SUCCEEDED", input: "ORIGINAL", output: "DONE", created_at: new Date().toISOString(), snapshot: { revision: 1, definition } }] : [] };
    if (path.endsWith("/events")) data = { items: events };
    await route.fulfill({ json: data });
  });
  await page.goto("/"); await page.getByRole("button", { name: "工作流", exact: true }).click();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
}

test("MCP JSON editor and invalid draft follow undo/redo of the same selected node", async ({ page }) => {
  await existingFlow(page, { nodes: [
    { id: "start", kind: "start", input: "input", parameters: {} },
    { id: "tool", kind: "mcp", name: "工具", input: "input", parameters: { arguments: { query: "original" } } },
    { id: "end", kind: "end", input: "tool", parameters: {} },
  ], edges: [{ source: "start", target: "tool" }, { source: "tool", target: "end" }] });
  await page.locator('.react-flow__node[data-id="tool"]').click();
  const editor = page.getByLabel("节点工具参数");
  await editor.fill('{"query":"changed"}');
  await page.getByRole("button", { name: "撤销编辑" }).click();
  expect(JSON.parse(await editor.inputValue())).toEqual({ query: "original" });
  await page.getByRole("button", { name: "重做编辑" }).click();
  expect(JSON.parse(await editor.inputValue())).toEqual({ query: "changed" });
  await editor.fill("{");
  await page.getByRole("button", { name: "撤销编辑" }).click();
  expect(JSON.parse(await editor.inputValue())).toEqual({ query: "original" });
  await expect(page.locator(".workflow-node-settings").getByRole("alert")).toHaveCount(0);
});

test("undo arrangement restores legacy empty-layout positions, not the latest canvas cache", async ({ page }) => {
  await existingFlow(page, { nodes: [
    { id: "start", kind: "start", input: "input", parameters: {} },
    { id: "step", kind: "transform", input: "input", parameters: { operation: "upper" } },
    { id: "end", kind: "end", input: "step", parameters: {} },
  ], edges: [{ source: "start", target: "step" }, { source: "step", target: "end" }] });
  const step = page.locator('.react-flow__node[data-id="step"]');
  const original = await step.getAttribute("style");
  await page.getByRole("button", { name: "整理画布", exact: true }).click();
  await expect.poll(() => step.getAttribute("style")).not.toBe(original);
  await page.getByRole("button", { name: "撤销编辑" }).click();
  await expect.poll(() => step.getAttribute("style")).toBe(original);
});

test("handled output is visible and reused as the newest explicit template test value", async ({ page }) => {
  const definition: WorkflowDefinition = { nodes: [
    { id: "start", kind: "start", input: "input", parameters: {} },
    { id: "source", kind: "transform", input: "input", parameters: { operation: "json_get", path: "missing" } },
    { id: "report", kind: "transform", input: "input", parameters: { operation: "template", template: "{{source}}" } },
    { id: "end", kind: "end", input: "report", parameters: {} },
  ], edges: [{ source: "start", target: "source" }, { source: "source", target: "report", case: "error" }, { source: "report", target: "end" }] };
  await existingFlow(page, definition, {}, [
    { event_id: 1, node_id: "source", event: "node.started", data: { input: "ORIGINAL" } },
    { event_id: 2, node_id: "source", event: "node.failed", data: { error_code: "NODE_EXECUTION_FAILED" } },
    { event_id: 3, node_id: "source", event: "node.handled", data: { output: '{"error_code":"NODE_EXECUTION_FAILED"}' } },
    { event_id: 4, node_id: "report", event: "node.started", data: { input: "ORIGINAL" } },
    { event_id: 5, node_id: "report", event: "node.completed", data: { output: "DONE" } },
  ]);
  await page.getByRole("button", { name: "运行历史", exact: true }).click();
  await page.locator('.workflow-history-item[data-run-id="previous"] > button').click();
  await page.getByRole("tab", { name: "变量", exact: true }).click();
  const source = page.locator('.workflow-variable[data-node-id="source"]');
  await source.locator("summary").click();
  await expect(source).toContainText('"error_code":"NODE_EXECUTION_FAILED"');
  const report = page.locator('.workflow-variable[data-node-id="report"]');
  await report.locator("summary").click();
  await report.getByRole("button", { name: "将输入用于节点测试", exact: true }).click();
  await expect(page.getByLabel("节点测试输入")).toHaveValue("ORIGINAL");
  await expect(page.getByLabel("变量 source 的测试值")).toHaveValue('{"error_code":"NODE_EXECUTION_FAILED"}');
});

test("empty secondary template values are explicit test inputs, not missing required fields", async ({ page }) => {
  await existingFlow(page, { nodes: [
    { id: "start", kind: "start", input: "input", parameters: {} },
    { id: "empty", kind: "transform", input: "input", parameters: { operation: "trim" } },
    { id: "report", kind: "transform", input: "input", parameters: { operation: "template", template: "{{input}}/{{empty}}" } },
    { id: "end", kind: "end", input: "report", parameters: {} },
  ], edges: [{ source: "start", target: "empty" }, { source: "empty", target: "report" }, { source: "report", target: "end" }] });
  let submitted: { text: string; variables: Record<string, string> } | undefined;
  await page.route("**/workflows/existing/nodes/report/test-runs", async route => {
    submitted = route.request().postDataJSON(); await route.fulfill({ json: { id: "single", output: "ROOT/" } });
  });
  await page.getByRole("button", { name: "测试文本转换节点", exact: true }).nth(1).click();
  await page.getByLabel("节点测试输入").fill("ROOT");
  await expect(page.getByLabel("变量 empty 的测试值")).toHaveValue("");
  await page.getByRole("button", { name: "运行单节点测试", exact: true }).click();
  await expect.poll(() => submitted).toEqual(expect.objectContaining({ text: "ROOT", variables: { empty: "" } }));
});
