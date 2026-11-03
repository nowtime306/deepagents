import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import type { WorkflowDefinition } from "../src/features/ask/workflow-editor";
import type { NodeEvent, WorkflowRun } from "../src/features/ask/workflow-execution";

const kinds = { start: "开始", end: "结束", llm: "大语言模型", agent: "智能体", rag: "知识检索", mcp: "MCP 工具",
  http: "网页读取", condition: "条件分支", transform: "文本转换", subworkflow: "子工作流", iteration: "数组迭代",
  parallel: "并行子流程", loop: "条件循环", approval: "人工审批", code: "Python代码" };

for (const kind of Object.keys(kinds) as (keyof typeof kinds)[]) test(`${kind} node has a direct isolated test action and visible results`, async ({ page }) => {
  const id = randomUUID(), work = ["approval", "code"].includes(kind);
  const node = { id: "target", kind, name: kinds[kind], input: "upstream", parameters: kind === "mcp" ? {
    arguments: { query: { ref: "upstream" }, context: { ref: "context" } },
  } : {} };
  const definition: WorkflowDefinition = { execution: work ? "work" : "readonly", nodes: [
    ...(kind === "start" ? [] : [{ id: "start", kind: "start" as const, input: "input", parameters: {} }]), node,
    ...(kind === "end" ? [] : [{ id: "end", kind: "end" as const, input: "target", parameters: {} }]),
  ], edges: [] };
  let requests = 0, submitted: { text: string; run_id: string; revision: number; variables: Record<string, string> } | null = null;
  let runs: WorkflowRun[] = [], events: NodeEvent[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let data: unknown = { items: [] };
    if (path === "/auth/me") data = { username: "节点验收", is_admin: true };
    if (path === "/models") data = { items: [{ id: "unused", name: "本机测试模型" }] };
    if (path === "/workflows") data = { items: [{ id, name: "节点独立调试", revision: 1, instructions: "", model_id: "unused", definition,
      layout: Object.fromEntries(definition.nodes.map((item, index) => [item.id, { x: 40 + 300 * index, y: 100 }])) }] };
    if (path === `/workflows/${id}/nodes/target/test-runs`) {
      requests++; submitted = route.request().postDataJSON();
      const body = submitted!;
      runs = [{ id: body.run_id, status: work ? "RUNNING" : "SUCCEEDED", work_status: work ? "WAITING_APPROVAL" : undefined,
        task_id: work ? "isolated-task" : undefined, input: body.text, output: work ? null : kind === "condition" ? "true" : "节点独立测试输出",
        created_at: new Date().toISOString(), snapshot: { revision: 1, debug_node_id: "target", debug_variables: body.variables, canvas_definition: definition, definition } }];
      events = [{ event_id: 1, node_id: "target", event: "node.started", data: { input: body.text } },
        ...(!work ? [{ event_id: 2, node_id: "target", event: "node.completed", data: { output: runs[0].output!, duration_ms: 12 } }] : [])];
      data = work ? { task_id: "isolated-task", workflow_run_id: body.run_id } : { id: body.run_id, output: runs[0].output };
    }
    if (path === `/workflows/${id}/runs`) data = { items: runs };
    if (path.endsWith("/events")) data = { items: events };
    await route.fulfill({ json: data });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "工作流", exact: true }).click();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const action = page.getByRole("button", { name: `测试${kinds[kind]}节点`, exact: true });
  await expect(action).toBeEnabled();
  await action.click();
  await expect(page.getByRole("heading", { name: `测试节点 · ${kinds[kind]}`, exact: true })).toBeVisible();
  await expect(page.getByText(/不会运行原图上游或下游/)).toBeVisible();
  await page.getByRole("textbox", { name: "节点测试输入", exact: true }).fill("明确的节点测试输入");
  if (kind === "mcp") await page.getByRole("textbox", { name: "变量 context 的测试值", exact: true }).fill("其他变量内容");
  await page.getByRole("button", { name: "运行单节点测试", exact: true }).click();
  const trace = page.getByRole("region", { name: "节点运行情况", exact: true });
  await expect(trace.getByLabel("节点实际输入", { exact: true })).toContainText("明确的节点测试输入");
  if (work) {
    await expect(trace).toContainText("等待审批");
    await expect(trace.getByRole("button", { name: "查看任务与审批", exact: true })).toBeVisible();
  } else {
    await expect(trace.getByLabel("节点实际输出", { exact: true })).toContainText(kind === "condition" ? "true" : "节点独立测试输出");
    await expect(trace).toContainText("节点耗时：12 ms");
  }
  if (kind === "condition") await expect(trace).toContainText("分支结果：命中");
  expect(requests).toBe(1);
  expect(submitted!.revision).toBe(1);
  expect(submitted!.variables).toEqual(kind === "mcp" ? { context: "其他变量内容" } : {});
  await expect(page.getByRole("button", { name: "发布版本", exact: true })).toBeDisabled();
  if (["transform", "mcp"].includes(kind)) {
    await expect(action).toBeEnabled();
    await action.click();
    await page.getByRole("textbox", { name: "节点测试输入", exact: true }).fill("手工修改");
    if (kind === "mcp") await page.getByRole("textbox", { name: "变量 context 的测试值", exact: true }).fill("");
    await page.getByRole("button", { name: "复用最近运行输入", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "节点测试输入", exact: true })).toHaveValue("明确的节点测试输入");
    if (kind === "mcp") await expect(page.getByRole("textbox", { name: "变量 context 的测试值", exact: true })).toHaveValue("其他变量内容");
    await page.getByRole("button", { name: "运行单节点测试", exact: true }).click();
    await expect.poll(() => requests).toBe(2);
    await expect(trace.getByLabel("节点实际输出", { exact: true })).toBeVisible();
  }
  if (kind === "transform") {
    await page.screenshot({ path: "../../.local/workflow-node-debug-desktop.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await expect(page.getByRole("textbox", { name: "节点测试输入", exact: true })).toBeVisible();
    await page.screenshot({ path: "../../.local/workflow-node-debug-mobile.png" });
  }
});
