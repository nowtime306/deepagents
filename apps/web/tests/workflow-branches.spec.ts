import { openCanvas, openWorkflowSchema } from "./workflow-test-ui";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";

const origin = "http://localhost:3100",
  headers = { Origin: origin };
async function login(page: Page) {
  const values = Object.fromEntries(
    readFileSync(
      resolve(process.cwd(), "../../services/api/.env.admin"),
      "utf8",
    )
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
}
function graph(kind: string, parameters: Record<string, unknown>) {
  return {
    nodes: [
      { id: "start", kind: "start" },
      { id: "action", kind, parameters },
      { id: "end", kind: "end", input: "action" },
    ],
    edges: [
      { source: "start", target: "action" },
      { source: "action", target: "end" },
    ],
  };
}

test("structured schema, iteration and parallel edit through the Chinese canvas", async ({
  page,
}) => {
  test.setTimeout(90000);
  page.setDefaultTimeout(10000);
  await login(page);
  const name = `流程组合页面验收-${Date.now()}`,
    created: string[] = [];
  const models = await (
    await page.request.get(`${origin}/api/v1/admin/models`)
  ).json();
  const model = models.items.find(
    (item: { kind: string; enabled: boolean }) =>
      item.kind === "chat" && item.enabled,
  ).id;
  try {
    const versions: string[] = [];
    for (const operation of ["upper", "lower"]) {
      const response = await page.request.post(`${origin}/api/v1/workflows`, {
        headers,
        data: {
          name: `${name}-${operation}`,
          model_id: model,
          definition: graph("transform", { operation }),
        },
      });
      expect(response.status()).toBe(201);
      const id = (await response.json()).id;
      created.push(id);
      writeFileSync(resolve(process.cwd(), "../../.local/workflow-browser-fixtures.json"), JSON.stringify({ prefix: name, ids: created }));
      expect(
        (
          await page.request.post(
            `${origin}/api/v1/workflows/${id}/test-runs`,
            { headers, data: { text: "Mixed" } },
          )
        ).status(),
      ).toBe(200);
      expect(
        (
          await page.request.post(`${origin}/api/v1/workflows/${id}/publish`, {
            headers,
            data: { revision: 1 },
          })
        ).status(),
      ).toBe(201);
      versions.push(
        (
          await (
            await page.request.get(`${origin}/api/v1/workflows/${id}/versions`)
          ).json()
        ).items[0].id,
      );
    }
    const response = await page.request.post(`${origin}/api/v1/workflows`, {
      headers,
      data: {
        name,
        model_id: model,
        definition: graph("iteration", {
          version_id: versions[0],
          max_items: 3,
        }),
      },
    });
    expect(response.status()).toBe(201);
    const root = (await response.json()).id;
    created.push(root);
    writeFileSync(resolve(process.cwd(), "../../.local/workflow-browser-fixtures.json"), JSON.stringify({ prefix: name, ids: created }));
    await page.getByRole("button", { name: "工作流", exact: true }).click();
    await openCanvas(page, name);
    await openWorkflowSchema(page);
    await page.getByLabel("输入 Schema", { exact: true }).fill("{");
    await expect(
      page.getByRole("button", { name: "保存草稿", exact: true }),
    ).toBeDisabled();
    await page.getByLabel("输入 Schema", { exact: true }).fill(
      JSON.stringify({
        type: "array",
        items: { type: "string" },
        maxItems: 3,
      }),
    );
    await page
      .getByLabel("输出 Schema", { exact: true })
      .fill(JSON.stringify({ type: "array", items: { type: "string" } }));
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.locator(".workflow-studio-header")).toContainText("修订 2");
    await openCanvas(page, name, "test");
    await page.getByRole("button", { name: "JSON输入", exact: true }).click();
    await page
      .getByRole("textbox", { name: "测试输入", exact: true })
      .fill('["Mixed","Hello"]');
    await page.getByRole("button", { name: "运行测试", exact: true }).click();
    await expect(page.locator(".workflow-run-output > pre")).toHaveText('["MIXED", "HELLO"]');
    await openCanvas(page, name);
    await page.locator('.react-flow__node[data-id="action"]').click();
    await expect(page.getByLabel("选择已发布子流程")).toHaveValue(versions[0]);
    await page.getByLabel("节点类型", { exact: true }).selectOption("parallel");
    await page.getByRole("listbox", { name: "并行子流程", exact: true }).selectOption(versions);
    if (!await page.getByLabel("输入 Schema", { exact: true }).isVisible())
      await openWorkflowSchema(page);
    await page
      .getByLabel("输入 Schema", { exact: true })
      .fill('{"type":"string"}');
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.locator(".workflow-studio-header")).toContainText("修订 3");
    await openCanvas(page, name, "test");
    await page.getByRole("textbox", { name: "测试输入", exact: true }).fill("Mixed");
    await page.getByRole("button", { name: "运行测试", exact: true }).click();
    await expect(page.locator(".workflow-run-output > pre")).toHaveText('["mixed", "MIXED"]');
    await page.screenshot({ path: resolve(process.cwd(), "../../.local/phase6-workflow-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect
      .poll(() =>
        page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 2,
        ),
      )
      .toBe(true);
    await page.screenshot({ path: resolve(process.cwd(), "../../.local/phase6-workflow-mobile.png"), fullPage: true });
  } finally {
    for (const id of created.reverse())
      expect(
        (
          await page.request.delete(`${origin}/api/v1/workflows/${id}`, {
            headers,
          })
        ).status(),
      ).toBe(200);
  }
});

test("fork and edited branch preserve source history and avoid external calls", async ({
  page,
}) => {
  test.setTimeout(90000);
  await login(page);
  const conversations = (
    await (await page.request.get(`${origin}/api/v1/conversations`)).json()
  ).items;
  let source: { id: string; title: string } | undefined;
  let history: { items: { role: string; content: unknown }[] } | undefined;
  for (const item of conversations) {
    const candidate = await (
      await page.request.get(
        `${origin}/api/v1/conversations/${item.id}/messages`,
      )
    ).json();
    if (
      candidate.items.some(
        (message: { role: string }) => message.role === "assistant",
      ) &&
      candidate.items.some(
        (message: { role: string }) => message.role === "user",
      ) &&
      !["PENDING", "RUNNING"].includes(candidate.latest_run?.status)
    ) {
      source = item;
      history = candidate;
      break;
    }
  }
  expect(source).toBeTruthy();
  const branches: string[] = [];
  try {
    await page
      .getByRole("button", { name: source!.title, exact: true })
      .nth(conversations.filter((item: { title: string }) => item.title === source!.title)
        .findIndex((item: { id: string }) => item.id === source!.id))
      .click();
    const forked = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/conversations/${source!.id}/branches`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "从此消息创建分支", exact: true })
      .last()
      .click();
    const forkResponse = await forked;
    expect(forkResponse.status()).toBe(201);
    const fork = (await forkResponse.json()).id;
    branches.push(fork);
    await expect(
      page.getByText("来自分支历史 · 仅作上下文").first(),
    ).toBeVisible();
    await expect
      .poll(
        async () =>
          (
            await (
              await page.request.get(
                `${origin}/api/v1/conversations/${fork}/usage`,
              )
            ).json()
          ).total.calls,
      )
      .toBe(0);
    await page
      .getByRole("button", { name: "编辑提问并创建分支", exact: true })
      .first()
      .click();
    const dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("修改后的提问")
      .fill("分支页面验收：修改后尚未发送的问题");
    const edited = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/conversations/${fork}/branches`) &&
        response.request().method() === "POST",
    );
    await dialog.getByRole("button", { name: "创建分支", exact: true }).click();
    const editResponse = await edited;
    expect(editResponse.status()).toBe(201);
    branches.push((await editResponse.json()).id);
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await expect(
      page.getByRole("textbox", { name: "发送消息", exact: true }),
    ).toHaveValue("分支页面验收：修改后尚未发送的问题");
    expect(
      (
        await (
          await page.request.get(
            `${origin}/api/v1/conversations/${source!.id}/messages`,
          )
        ).json()
      ).items,
    ).toEqual(history!.items);
    await page.reload();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect
      .poll(() =>
        page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 2,
        ),
      )
      .toBe(true);
  } finally {
    for (const id of branches.reverse())
      expect(
        (
          await page.request.delete(`${origin}/api/v1/conversations/${id}`, {
            headers,
          })
        ).status(),
      ).toBe(200);
  }
});
