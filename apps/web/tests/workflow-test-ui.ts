import { expect, type Page } from "@playwright/test";

export async function createCanvas(page: Page, name: string, model?: string, template = "llm") {
  await page.getByRole("button", { name: "新建工作流", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "创建工作流", exact: true });
  await dialog.getByLabel("工作流名称", { exact: true }).fill(name);
  if (model) await dialog.getByLabel("工作流聊天模型", { exact: true }).selectOption(model);
  await dialog.getByLabel("工作流初始模板", { exact: true }).selectOption(template);
  const created = page.waitForResponse((response) => response.url().endsWith("/api/v1/workflows") && response.request().method() === "POST");
  await dialog.getByRole("button", { name: "进入画布", exact: true }).click();
  const result = await created;
  expect(result.status()).toBe(201);
  return (await result.json()).id as string;
}

export async function openCanvas(page: Page, name: string, mode: "edit" | "test" = "edit") {
  const studio = page.getByRole("region", { name: "工作流工作台", exact: true });
  if (await studio.isVisible() && await studio.getByRole("heading", { name, exact: true }).isVisible()) {
    const edit = page.getByRole("button", { name: "返回编辑", exact: true });
    if (await edit.isVisible()) await edit.click();
  } else {
    await page.locator(".workflow-library-card").filter({ has: page.getByText(name, { exact: true }) }).getByRole("button", { name: mode === "edit" ? "编辑" : "测试或运行", exact: true }).click();
  }
  if (mode === "test") await page.getByRole("button", { name: "测试运行", exact: true }).click();
  else {
    const action = page.locator('.react-flow__node').filter({ has: page.locator('.workflow-node:not([data-kind="start"]):not([data-kind="end"])') }).first();
    if (await action.count()) await action.click();
  }
}

export async function openWorkflowSchema(page: Page) {
  if (!await page.getByLabel("工作流名称", { exact: true }).isVisible())
    await page.getByRole("button", { name: "流程设置", exact: true }).click();
  if (!await page.getByLabel("输入 Schema", { exact: true }).isVisible())
    await page.getByText("输入输出结构与共享预算", { exact: true }).click();
}
