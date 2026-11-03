import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const origin = "http://localhost:3100",
  headers = { Origin: origin };

test("structured forms validate fields, preserve JSON and follow the selected published schema", async ({
  page,
}) => {
  test.setTimeout(90000);
  const credentials = Object.fromEntries(
    readFileSync(resolve("../../services/api/.env.admin"), "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const index = line.indexOf("=");
        return [line.slice(0, index), line.slice(index + 1)];
      }),
  );
  await page.goto(origin);
  await page
    .getByLabel("账号", { exact: true })
    .fill(credentials.ADMIN_USERNAME);
  await page
    .getByLabel("密码", { exact: true })
    .fill(credentials.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  const name = `工作流表单验收-${Date.now()}`;
  const model = (
    await (await page.request.get(`${origin}/api/v1/models`)).json()
  ).items[0].id;
  const schema = {
    type: "object",
    title: "申请资料",
    properties: {
      name: { type: "string", title: "姓名", minLength: 2, maxLength: 20 },
      age: { type: "integer", title: "年龄", minimum: 18, maximum: 60 },
      confirmed: { type: "boolean", title: "已确认" },
      tier: { type: "string", title: "档位", enum: ["普通", "高级"] },
      tags: {
        type: "array",
        title: "标签",
        items: { type: "string", minLength: 1 },
        minItems: 1,
        maxItems: 2,
      },
      address: {
        type: "object",
        title: "地址",
        properties: { city: { type: "string", title: "城市", minLength: 2 } },
        required: ["city"],
      },
      policy: {
        type: "object",
        title: "策略",
        properties: { level: { type: "string" }, ready: { type: "boolean" } },
        required: ["level", "ready"],
        enum: [{ level: "safe", ready: true }],
      },
      note: { type: "string", title: "备注" },
    },
    required: ["name", "age", "confirmed", "tier", "tags", "address", "policy"],
  };
  const definition = {
    input_schema: schema,
    output_schema: schema,
    nodes: [
      { id: "start", kind: "start" },
      { id: "copy", kind: "transform", parameters: { operation: "text" } },
      { id: "end", kind: "end", input: "copy" },
    ],
    edges: [
      { source: "start", target: "copy" },
      { source: "copy", target: "end" },
    ],
  };
  const response = await page.request.post(`${origin}/api/v1/workflows`, {
    headers,
    data: { name, model_id: model, definition },
  });
  expect(response.status()).toBe(201);
  const id = (await response.json()).id;
  writeFileSync(
    resolve("../../.local/workflow-form-fixtures.json"),
    JSON.stringify({ prefix: name, ids: [id] }),
  );
  try {
    await page.getByRole("button", { name: "工作流", exact: true }).click();
    await page
      .locator(".run-history-card")
      .filter({ hasText: name })
      .getByRole("button", { name: "测试或运行", exact: true })
      .click();
    const form = page.locator("form").filter({
      has: page.getByRole("heading", { name: `测试：${name}`, exact: true }),
    });
    const run = form.getByRole("button", { name: "运行测试", exact: true });
    await expect(run).toBeDisabled();
    await form
      .getByRole("button", { name: "按结构新建输入", exact: true })
      .click();
    await form
      .getByRole("textbox", { name: "申请资料 / 姓名", exact: true })
      .fill("😊");
    await expect(form.getByRole("alert")).toContainText("文字长度");
    await form
      .getByRole("textbox", { name: "申请资料 / 姓名", exact: true })
      .fill("张三");
    await form
      .getByRole("spinbutton", { name: "申请资料 / 年龄", exact: true })
      .fill("17");
    await expect(run).toBeDisabled();
    await form
      .getByRole("spinbutton", { name: "申请资料 / 年龄", exact: true })
      .fill("25");
    await form
      .getByRole("combobox", { name: "申请资料 / 已确认", exact: true })
      .selectOption("true");
    await form
      .getByRole("combobox", { name: "申请资料 / 档位", exact: true })
      .selectOption({ label: "高级" });
    await form
      .getByRole("button", { name: "添加申请资料 / 标签项", exact: true })
      .click();
    await form
      .getByRole("textbox", { name: "申请资料 / 标签 / 第1项", exact: true })
      .fill("研发");
    await form
      .getByRole("button", { name: "添加申请资料 / 标签项", exact: true })
      .click();
    await expect(
      form.getByRole("button", { name: "添加申请资料 / 标签项", exact: true }),
    ).toBeDisabled();
    await form
      .getByRole("button", { name: "删除申请资料 / 标签第2项", exact: true })
      .click();
    await form
      .getByRole("textbox", { name: "申请资料 / 地址 / 城市", exact: true })
      .fill("上海");
    await form.getByRole("checkbox", { name: "启用备注", exact: true }).check();
    await form
      .getByRole("textbox", { name: "申请资料 / 备注", exact: true })
      .fill("不应发送的可选字段");
    await form
      .getByRole("checkbox", { name: "启用备注", exact: true })
      .uncheck();
    await expect(run).toBeEnabled();
    await form.getByRole("button", { name: "JSON输入", exact: true }).click();
    const raw = form.getByRole("textbox", { name: "测试输入", exact: true });
    const expected = JSON.parse(await raw.inputValue());
    expect(expected).toMatchObject({
      name: "张三",
      age: 25,
      confirmed: true,
      tier: "高级",
      tags: ["研发"],
      address: { city: "上海" },
    });
    expect(expected).not.toHaveProperty("note");
    await raw.fill("{坏");
    await form.getByRole("button", { name: "表单输入", exact: true }).click();
    await expect(form.getByRole("alert")).toContainText("不是有效JSON");
    await expect(
      form.getByRole("textbox", { name: "申请资料 / 姓名", exact: true }),
    ).toHaveCount(0);
    await form.getByRole("button", { name: "JSON输入", exact: true }).click();
    await expect(raw).toHaveValue("{坏");
    await raw.fill(
      JSON.stringify({ ...expected, constructor: "拒绝未知字段" }),
    );
    await form.getByRole("button", { name: "表单输入", exact: true }).click();
    await expect(form.getByRole("alert")).toContainText("未声明字段");
    await form.getByRole("button", { name: "JSON输入", exact: true }).click();
    expected.policy = { ready: true, level: "safe" };
    await raw.fill(JSON.stringify(expected));
    await form.getByRole("button", { name: "表单输入", exact: true }).click();
    await expect(run).toBeEnabled();
    expect(
      (
        await (
          await page.request.get(`${origin}/api/v1/workflows/${id}/runs`)
        ).json()
      ).items,
    ).toHaveLength(0);
    const finished = page.waitForResponse(
      (item) =>
        item.url().endsWith(`/workflows/${id}/test-runs`) &&
        item.request().method() === "POST",
    );
    await run.click();
    const executed = await finished;
    expect(executed.status()).toBe(200);
    expect(JSON.parse((await executed.json()).output)).toEqual(expected);
    expect(
      (
        await page.request.post(`${origin}/api/v1/workflows/${id}/publish`, {
          headers,
          data: { revision: 1 },
        })
      ).status(),
    ).toBe(201);
    expect(
      (
        await page.request.put(`${origin}/api/v1/workflows/${id}`, {
          headers,
          data: {
            name,
            model_id: model,
            revision: 1,
            definition: {
              ...definition,
              input_schema: { type: "integer", minimum: 10 },
              output_schema: { type: "string" },
            },
          },
        })
      ).status(),
    ).toBe(200);
    await page.reload();
    await page.getByRole("button", { name: "工作流", exact: true }).click();
    await page
      .locator(".run-history-card")
      .filter({ hasText: name })
      .getByRole("button", { name: "测试或运行", exact: true })
      .click();
    await form
      .getByRole("button", { name: "按结构新建输入", exact: true })
      .click();
    await expect(
      form.getByRole("spinbutton", { name: "输入", exact: true }),
    ).toHaveValue("10");
    await form
      .getByRole("combobox", { name: "运行工作流版本", exact: true })
      .selectOption("1");
    await form.getByRole("button", { name: "JSON输入", exact: true }).click();
    await raw.fill(JSON.stringify(expected));
    await form.getByRole("button", { name: "表单输入", exact: true }).click();
    await expect(
      form.getByRole("textbox", { name: "申请资料 / 姓名", exact: true }),
    ).toHaveValue("张三");
    const published = page.waitForResponse(
      (item) =>
        item.url().endsWith(`/workflows/${id}/versions/1/runs`) &&
        item.request().method() === "POST",
    );
    await form
      .getByRole("button", { name: "运行已发布版本", exact: true })
      .click();
    expect((await published).status()).toBe(200);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect
      .poll(() =>
        page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      )
      .toBeTruthy();
    writeFileSync(
      resolve("../../.local/workflow-form-mobile-layout.json"),
      JSON.stringify(
        await form.evaluate((element) => {
          const bounds = element.getBoundingClientRect();
          return {
            width: element.clientWidth,
            scroll: element.scrollWidth,
            overflow: [...element.querySelectorAll("*")]
              .filter(
                (child) =>
                  child.getBoundingClientRect().right > bounds.right + 1,
              )
              .slice(0, 12)
              .map((child) => ({
                tag: child.tagName,
                class: child.className,
                width: child.getBoundingClientRect().width,
                right: child.getBoundingClientRect().right - bounds.right,
              })),
          };
        }),
      ),
    );
    await expect
      .poll(() =>
        form.evaluate(
          (element) => element.scrollWidth <= element.clientWidth + 1,
        ),
      )
      .toBeTruthy();
    await form
      .getByRole("spinbutton", { name: "申请资料 / 年龄", exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({ path: "../../.local/workflow-form-mobile.png" });
  } finally {
    expect(
      (
        await page.request.delete(`${origin}/api/v1/workflows/${id}`, {
          headers,
        })
      ).status(),
    ).toBe(200);
  }
});
