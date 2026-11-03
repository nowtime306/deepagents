import { expect, test, type Page } from "@playwright/test";
import type { WorkflowDefinition } from "../src/features/ask/workflow-editor";

const graph = (number: number): WorkflowDefinition => ({ nodes: [
  { id: "start", kind: "start", input: "input", parameters: {} },
  { id: "action", kind: "transform", name: `处理 v${number}`, input: "input", parameters: { operation: number === 1 ? "upper" : "lower" } },
  { id: "end", kind: "end", input: "action", parameters: {} },
], edges: [{ source: "start", target: "action" }, { source: "action", target: "end" }] });

async function setup(page: Page, options: { readonly?: boolean; legacy?: boolean; delayed?: boolean; refreshFailure?: boolean; conflict?: boolean } = {}) {
  let flow = { id: "flow", name: "版本示例", model_id: "unused", instructions: "草稿指令", revision: 3,
    tested_revision: 3 as number | null, definition: graph(2), layout: { action: { x: 500, y: 200 } }, current_published_version: 2, release_revision: 3 };
  const versions = [2, 1].map(number => ({ version: number, name: `历史名称 v${number}`, model_id: "unused", instructions: `历史指令 v${number}`,
    draft_revision: number, active: true, input_schema: { type: "string" }, execution: "readonly", created_at: "2026-11-03T00:00:00Z",
    definition: options.legacy && number === 1 ? { nodes: [{ id: "start", kind: "start" }, { id: "action", kind: "transform", parameters: { operation: "upper" } }, { id: "end", kind: "end", input: "action" }], edges: graph(1).edges } : graph(number),
    canvas_definition: graph(number), schema_supported: true, layout_known: !options.legacy || number !== 1,
    layout: options.legacy && number === 1 ? {} : { action: { x: number * 15, y: number * 30 } } }));
  const requests: { path: string; method: string; body: Record<string, unknown> | null }[] = [];
  let committed = false;
  await page.route("**/api/v1/**", async route => {
    const request = route.request(), method = request.method(), path = new URL(request.url()).pathname.replace("/api/v1", "");
    const body = request.postData() ? request.postDataJSON() : null;
    requests.push({ path, method, body });
    let data: unknown = { items: [] }, status = 200;
    if (path === "/auth/me") data = { username: "版本组件测试", is_admin: !options.readonly, ...(options.readonly ? { permissions: ["workflows.read"] } : {}) };
    if (path === "/models") data = { items: [{ id: "unused", name: "不调用的模型" }] };
    if (path === "/workflows") data = { items: [flow] };
    if (path === "/workflows/flow" && method === "PUT") { flow = { ...flow, ...body, revision: flow.revision + 1 }; data = { id: "flow" }; }
    if (path === "/workflows/flow" && method === "GET") data = flow;
    if (path === "/workflows/flow/versions") data = { items: versions, current_published_version: flow.current_published_version, release_revision: flow.release_revision };
    const detail = path.match(/^\/workflows\/flow\/versions\/(\d+)$/);
    if (detail) { if (options.delayed && detail[1] === "1") await new Promise(resolve => setTimeout(resolve, 200)); data = versions.find(item => item.version === Number(detail[1])); }
    if (path.endsWith("/set-current")) { flow = { ...flow, current_published_version: Number(path.split("/").at(-2)), release_revision: flow.release_revision + 1 }; data = { current_published_version: flow.current_published_version, release_revision: flow.release_revision }; }
    if (path.endsWith("/restore")) {
      if (options.conflict) { status = 409; data = { error: { message: "草稿已改变，未恢复或创建备份" } }; }
      else { const source = versions.find(item => item.version === Number(path.split("/").at(-2)))!; flow = { ...flow, name: source.name, instructions: source.instructions, definition: source.canvas_definition, layout: source.layout as typeof flow.layout, revision: flow.revision + 1, tested_revision: null }; data = { id: "flow", revision: flow.revision, workflow: structuredClone(flow), draft_backup_id: "backup", draft_backup_name: "恢复前草稿备份" }; committed = true; }
    }
    if (path.endsWith("/copy")) { data = { id: "copy", revision: 1 }; status = 201; committed = true; }
    if (options.refreshFailure && committed && (path === "/workflows" || path === "/workflows/flow/versions")) { status = 503; data = { error: { message: "测试用读取失败" } }; }
    if (path === "/workflows/flow/runs") data = { items: [{ id: "old-run", status: "SUCCEEDED", input: "before", output: "AFTER", created_at: "2026-11-03T00:00:00Z", snapshot: { revision: 1, definition: { ...graph(1), nodes: graph(1).nodes.map(node => node.id === "action" ? { ...node, name: "历史运行节点" } : node) } } }] };
    if (path.endsWith("/events")) data = { items: [] };
    await route.fulfill({ status, json: data });
  });
  await page.goto("/"); await page.getByRole("button", { name: "工作流", exact: true }).click();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByRole("button", { name: "版本历史", exact: true }).click();
  const panel = page.getByRole("region", { name: "工作流版本历史", exact: true });
  await expect(panel.getByRole("button", { name: "预览 v1", exact: true })).toBeVisible();
  return { panel, requests, state: () => flow };
}

test("historical preview is read-only and returning preserves unsaved draft", async ({ page }) => {
  const { panel, requests } = await setup(page);
  await page.getByRole("button", { name: "流程设置", exact: true }).click();
  await page.getByLabel("模型指令", { exact: true }).fill("未保存的本地指令");
  await page.getByRole("button", { name: "版本历史", exact: true }).click();
  await panel.getByRole("button", { name: "预览 v1", exact: true }).click();
  await expect(panel).toContainText("历史指令 v1");
  await expect(page.getByRole("button", { name: "保存草稿", exact: true })).toBeDisabled();
  await panel.getByRole("button", { name: "返回编辑草稿", exact: true }).click();
  await page.getByRole("button", { name: "流程设置", exact: true }).click();
  await expect(page.getByLabel("模型指令", { exact: true })).toHaveValue("未保存的本地指令");
  expect(requests.filter(item => item.method !== "GET")).toEqual([]);
});

test("setting current v1 selects an explicit v1 execution, not v2", async ({ page }) => {
  const { panel, requests } = await setup(page);
  await panel.getByRole("button", { name: "预览 v1", exact: true }).click();
  await panel.getByRole("button", { name: "设为当前发布版", exact: true }).click();
  await expect(panel).toContainText("当前发布版 v1");
  await page.getByRole("button", { name: "运行当前发布版", exact: true }).click();
  await expect(page.getByLabel("运行工作流版本")).toHaveValue("1");
  await page.getByLabel("测试输入", { exact: true }).fill("Hello");
  await page.getByRole("button", { name: "运行已发布版本", exact: true }).click();
  expect(requests.some(item => item.path === "/workflows/flow/versions/1/runs" && item.method === "POST")).toBe(true);
  expect(requests.find(item => item.path.endsWith("/set-current"))?.body).toEqual({ release_revision: 3 });
});

test("dirty restore saves first, uses its new revision and exposes the persistent backup", async ({ page }) => {
  const { panel, requests, state } = await setup(page);
  await page.getByRole("button", { name: "流程设置", exact: true }).click(); await page.getByLabel("模型指令", { exact: true }).fill("应当保留的编辑");
  await page.getByRole("button", { name: "版本历史", exact: true }).click(); await panel.getByRole("button", { name: "预览 v1", exact: true }).click();
  await panel.getByRole("button", { name: "恢复为草稿", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "备份当前草稿并恢复", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0); await expect(page.locator(".workflow-version-linked")).toContainText("恢复前草稿备份");
  const writes = requests.filter(item => item.method !== "GET");
  expect(writes.map(item => item.method)).toEqual(["PUT", "POST"]); expect(writes[0].body?.instructions).toBe("应当保留的编辑"); expect(writes[1].body).toEqual({ revision: 4 });
  expect(state().tested_revision).toBeNull(); expect(state().current_published_version).toBe(2);
});

for (const operation of ["restore", "copy"] as const) test(`${operation} committed then refresh failed does not invite a duplicate mutation`, async ({ page }) => {
  const { panel, requests } = await setup(page, { refreshFailure: true }); await panel.getByRole("button", { name: "预览 v1", exact: true }).click();
  await panel.getByRole("button", { name: operation === "restore" ? "恢复为草稿" : "复制为新工作流", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: operation === "restore" ? "备份当前草稿并恢复" : "创建独立副本", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0); await expect(page.getByRole("alert")).toContainText(operation === "restore" ? "恢复已成功" : "已创建");
  await expect(page.locator(".workflow-version-linked")).toBeVisible(); expect(requests.filter(item => item.path.endsWith(`/${operation}`))).toHaveLength(1);
});

test("legacy empty layout uses defaults and a stale preview cannot replace the selected snapshot", async ({ page }) => {
  const { panel } = await setup(page, { legacy: true, delayed: true }); const draftPosition = await page.locator('.react-flow__node[data-id="action"]').getAttribute("style");
  await panel.getByRole("button", { name: "预览 v1", exact: true }).click(); await panel.getByRole("button", { name: "预览 v2", exact: true }).click();
  await expect(panel).toContainText("v2 配置预览"); await page.waitForTimeout(260); await expect(panel).not.toContainText("v1 配置预览");
  await panel.getByRole("button", { name: "预览 v1", exact: true }).click(); await expect(panel).toContainText("未保存自定义布局");
  await expect.poll(() => page.locator('.react-flow__node[data-id="action"]').getAttribute("style")).not.toBe(draftPosition);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
});

test("read-only viewers have no version mutations and narrow layout fits", async ({ page }) => {
  const { panel, requests } = await setup(page, { readonly: true }); await panel.getByRole("button", { name: "预览 v1", exact: true }).click();
  await expect(panel).toContainText("v1 配置预览"); await expect(panel.getByRole("button", { name: "恢复为草稿", exact: true })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "设为当前发布版", exact: true })).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(requests.filter(item => item.method !== "GET")).toEqual([]);
});

test("opening an old run clears a configuration preview before tracing its own graph", async ({ page }) => {
  const { panel } = await setup(page); await panel.getByRole("button", { name: "预览 v2", exact: true }).click(); await expect(panel).toContainText("v2 配置预览");
  await page.getByRole("button", { name: "运行历史", exact: true }).click(); await page.locator('.workflow-history-item[data-run-id="old-run"] > button').click();
  await expect(page.locator('.react-flow__node[data-id="action"]')).toContainText("历史运行节点");
  await expect(page.locator(".workflow-snapshot-banner")).toContainText("运行时画布快照");
});
