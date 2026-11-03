import { openCanvas } from "./workflow-test-ui";
import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const origin = "http://localhost:3100",
  headers = { Origin: origin };
function graph(parameters: Record<string, unknown>) {
  return {
    nodes: [
      { id: "start", kind: "start" },
      { id: "action", kind: "transform", parameters },
      { id: "end", kind: "end", input: "action" },
    ],
    edges: [
      { source: "start", target: "action" },
      { source: "action", target: "end" },
    ],
  };
}

test("Chinese conditional loop, real error outlet drawing and visible round evidence", async ({
  page,
}) => {
  test.setTimeout(90000);
  const values = Object.fromEntries(
    readFileSync(resolve("../../services/api/.env.admin"), "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const index = line.indexOf("=");
        return [line.slice(0, index), line.slice(index + 1)];
      }),
  );
  await page.goto(origin);
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  const name = `条件循环页面验收-${Date.now()}`,
    ids: string[] = [];
  const model = (
    await (await page.request.get(`${origin}/api/v1/models`)).json()
  ).items[0].id;
  async function create(label: string, definition: ReturnType<typeof graph>) {
    const response = await page.request.post(`${origin}/api/v1/workflows`, {
      headers,
      data: { name: label, model_id: model, definition },
    });
    expect(response.status()).toBe(201);
    const id = (await response.json()).id;
    ids.push(id);
    writeFileSync(
      resolve("../../.local/workflow-loop-fixtures.json"),
      JSON.stringify({ prefix: name, ids }),
    );
    return id;
  }
  try {
    const leaf = await create(
      name + "-子流程",
      graph({ operation: "replace", old: "aa", new: "a" }),
    );
    expect(
      (
        await page.request.post(
          `${origin}/api/v1/workflows/${leaf}/test-runs`,
          { headers, data: { text: "aa" } },
        )
      ).status(),
    ).toBe(200);
    expect(
      (
        await page.request.post(`${origin}/api/v1/workflows/${leaf}/publish`, {
          headers,
          data: { revision: 1 },
        })
      ).status(),
    ).toBe(201);
    const version = (
      await (
        await page.request.get(`${origin}/api/v1/workflows/${leaf}/versions`)
      ).json()
    ).items[0].id;
    const root = await create(name, graph({ operation: "text" }));
    await page.getByRole("button", { name: "工作流", exact: true }).click();
    await openCanvas(page, name);
    await page.locator('.react-flow__node[data-id="action"]').click();
    await page.getByLabel("节点类型", { exact: true }).selectOption("loop");
    await page
      .getByLabel("选择已发布子流程", { exact: true })
      .selectOption(version);
    await page.getByLabel("最多循环轮数", { exact: true }).fill("3");
    await page
      .getByLabel("条件判断方式", { exact: true })
      .selectOption("equals");
    await page.getByLabel("条件比较内容", { exact: true }).fill("a");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.locator(".workflow-studio-header")).toContainText("修订 2");
    await openCanvas(page, name, "test");
    const form = page.locator("form").filter({
      has: page.getByRole("heading", { name: `测试：${name}`, exact: true }),
    });
    await form
      .getByRole("textbox", { name: "测试输入", exact: true })
      .fill("aaaaaaaa");
    await form.getByRole("button", { name: "运行测试", exact: true }).click();
    await expect(form.locator(".workflow-run-output > pre")).toHaveText("a");
    await expect(
      form.locator("summary").filter({ hasText: "第3/3轮 · 已满足停止条件" }),
    ).toBeVisible();
    await openCanvas(page, name);
    await page.locator('.react-flow__node[data-id="action"]').click();
    await page.getByLabel("最多循环轮数", { exact: true }).fill("2");
    await page.getByText("画布选项", { exact: true }).click();
    await page
      .getByLabel("新增节点类型", { exact: true })
      .selectOption("transform");
    await page.locator(".workflow-canvas-options").getByRole("button", { name: "添加节点", exact: true }).click();
    await page.getByText("画布选项", { exact: true }).click();
    await page
      .getByLabel("节点输入来源", { exact: true })
      .selectOption("action");
    await page
      .getByLabel("文本转换方式", { exact: true })
      .selectOption("json_get");
    await page
      .getByRole("textbox", { name: "字段路径", exact: true })
      .fill("error_code");
    await page.getByRole("button", { name: "适应画布", exact: true }).click();
    const recovery = page.locator('.react-flow__node[data-id^="transform_"]');
    const error = page.locator(
      '.react-flow__node[data-id="action"] .react-flow__handle[data-handleid="error"]',
    );
    await error.dragTo(recovery.locator(".react-flow__handle.target"));
    await recovery
      .locator('.react-flow__handle[data-handleid="out"]')
      .dragTo(
        page.locator(
          '.react-flow__node[data-id="end"] .react-flow__handle.target',
        ),
      );
    await expect(
      page.getByRole("group", { name: /从 action 到 transform_/ }),
    ).toHaveCount(1);
    await page.locator('.react-flow__node[data-id="end"]').click();
    await page
      .getByRole("checkbox", { name: "输出当前分支结果", exact: true })
      .check();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.locator(".workflow-studio-header")).toContainText("修订 3");
    const saved = (
      await (await page.request.get(`${origin}/api/v1/workflows`)).json()
    ).items.find((item: { id: string }) => item.id === root);
    expect(
      saved.definition.edges.some(
        (edge: { source: string; case: string }) =>
          edge.source === "action" && edge.case === "error",
      ),
    ).toBeTruthy();
    await openCanvas(page, name, "test");
    await form
      .getByRole("textbox", { name: "测试输入", exact: true })
      .fill("aaaaaaaa");
    await form.getByRole("button", { name: "运行测试", exact: true }).click();
    await expect(form.locator(".workflow-run-output > pre")).toHaveText("WORKFLOW_LOOP_LIMIT");
    await expect(
      form.locator("summary").filter({ hasText: "已转入错误分支" }),
    ).toBeVisible();
    const failure = form
      .locator("summary")
      .filter({ hasText: "条件循环 · 执行失败" });
    await failure.click();
    const loopError = failure.locator("..").getByRole("status");
    await expect(loopError).toContainText("已达到循环次数上限，仍未满足停止条件；未自动继续。");
    await expect(loopError).toContainText("WORKFLOW_LOOP_LIMIT");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect
      .poll(() =>
        page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      )
      .toBeTruthy();
    await page.screenshot({ path: "../../.local/workflow-loop-mobile.png" });
  } finally {
    for (const id of ids.reverse())
      expect(
        (
          await page.request.delete(`${origin}/api/v1/workflows/${id}`, {
            headers,
          })
        ).status(),
      ).toBe(200);
  }
});
