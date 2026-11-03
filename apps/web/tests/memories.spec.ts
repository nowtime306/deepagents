import { expect, test } from "@playwright/test";
import { createServer } from "node:http";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

test("memory review, scoped semantic recall, metering and source deletion", async ({
  page,
  browser,
}) => {
  test.setTimeout(90000);
  page.setDefaultTimeout(15000);
  const origin = "http://localhost:3100",
    headers = { Origin: origin };
  const stamp = Date.now(),
    name = `记忆页面验收-${stamp}`;
  const username = `acct-test-memory-${stamp}`,
    password = "Memory-validation-2026";
  const models: string[] = [],
    runs: string[] = [];
  let user = "",
    invitation = "",
    conversation = "",
    project = "",
    memory = "",
    vectors = 0;
  const stub = createServer(async (request, response) => {
    let raw = "";
    for await (const part of request) raw += part;
    const body = JSON.parse(raw);
    if (request.url === "/v1/embeddings") {
      vectors++;
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(
        JSON.stringify({
          data: body.input.map((_: string, index: number) => ({
            index,
            embedding: [1, 0],
          })),
          usage: { prompt_tokens: 6, total_tokens: 6 },
        }),
      );
      return;
    }
    const source = body.messages.findLast(
      (message: { role: string }) => message.role === "user",
    );
    const prefix = "以下是经用户授权的来源消息数据：\n";
    const sources =
      typeof source.content === "string" && source.content.startsWith(prefix)
        ? JSON.parse(source.content.slice(prefix.length))
        : null;
    const content = sources
      ? JSON.stringify({
          items: [
            {
              title: "语言偏好",
              content: "用户希望文档使用中文",
              source_message_ids: [sources[0].id],
            },
          ],
        })
      : "已收到你的偏好。";
    const id = `chatcmpl-memory-${Date.now()}`,
      usage = { prompt_tokens: 7, completion_tokens: 3, total_tokens: 10 };
    if (!body.stream) {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(
        JSON.stringify({
          id,
          object: "chat.completion",
          created: 1,
          model: body.model,
          usage,
          choices: [
            {
              index: 0,
              finish_reason: "stop",
              message: { role: "assistant", content },
            },
          ],
        }),
      );
    } else {
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      const send = (choices: unknown[], extra = {}) =>
        response.write(
          "data: " +
            JSON.stringify({
              id,
              object: "chat.completion.chunk",
              created: 1,
              model: body.model,
              choices,
              ...extra,
            }) +
            "\n\n",
        );
      send([
        {
          index: 0,
          delta: { role: "assistant", content },
          finish_reason: null,
        },
      ]);
      send([{ index: 0, delta: {}, finish_reason: "stop" }]);
      send([], { usage });
      response.end("data: [DONE]\n\n");
    }
  });
  await new Promise<void>((done) => stub.listen(0, "127.0.0.1", done));
  const port = (stub.address() as { port: number }).port;
  const admin = await browser.newContext();
  const credentials = Object.fromEntries(
    readFileSync(resolve("../../services/api/.env.admin"), "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const i = line.indexOf("=");
        return [line.slice(0, i), line.slice(i + 1)];
      }),
  );
  try {
    expect(
      (
        await admin.request.post(`${origin}/api/v1/auth/login`, {
          headers,
          data: {
            username: credentials.ADMIN_USERNAME,
            password: credentials.ADMIN_PASSWORD,
          },
        })
      ).ok(),
    ).toBeTruthy();
    for (const kind of ["chat", "embedding"]) {
      const created = await admin.request.post(
        `${origin}/api/v1/admin/models`,
        {
          headers,
          data: {
            name: name + "-" + kind,
            kind,
            model: "local-memory-only",
            base_url: `http://127.0.0.1:${port}/v1`,
            api_key: "local-double-only",
            ...(kind === "chat" ? { max_output_tokens: 4096 } : {}),
          },
        },
      );
      expect(created.status()).toBe(201);
      models.push((await created.json()).id);
    }
    const invited = await admin.request.post(
      `${origin}/api/v1/admin/invitations`,
      { headers, data: { name, role_id: "member", valid_days: 1 } },
    );
    expect(invited.status()).toBe(201);
    const token = await invited.json();
    invitation = token.id;
    const registered = await page.request.post(
      `${origin}/api/v1/auth/register`,
      { headers, data: { username, password, invitation: token.invitation } },
    );
    expect(registered.status()).toBe(201);
    user = (
      await (
        await admin.request.get(`${origin}/api/v1/admin/accounts?q=${username}`)
      ).json()
    ).items[0].id;
    expect(
      (
        await page.request.post(`${origin}/api/v1/auth/login`, {
          headers,
          data: { username, password },
        })
      ).ok(),
    ).toBeTruthy();
    project = (
      await (
        await page.request.post(`${origin}/api/v1/conversation-projects`, {
          headers,
          data: { name },
        })
      ).json()
    ).id;
    const createdConversation = await page.request.post(
      `${origin}/api/v1/conversations`,
      {
        headers,
        data: { title: name },
      },
    );
    expect(createdConversation.status()).toBe(201);
    conversation = (await createdConversation.json()).id;
    const started = await page.request.post(
      `${origin}/api/v1/conversations/${conversation}/runs`,
      {
        headers,
        data: { model_id: models[0], message: "我希望所有文档使用中文。" },
      },
    );
    expect(started.status(), await started.text()).toBe(202);
    runs.push((await started.json()).run_id);
    await expect
      .poll(
        async () =>
          (
            await (
              await page.request.get(`${origin}/api/v1/runs/${runs[0]}`)
            ).json()
          ).status,
        { timeout: 20000 },
      )
      .toBe("SUCCEEDED");
    await page.goto(origin);
    await page.getByRole("button", { name: "能力中心", exact: true }).click();
    await page.getByRole("button", { name: "长期记忆", exact: true }).click();
    await page.getByRole("button", { name: "从会话生成记忆候选", exact: true }).click();
    await page
      .getByLabel("来源会话", { exact: true })
      .selectOption(conversation);
    await page.getByLabel("提取模型", { exact: true }).selectOption(models[0]);
    await page
      .getByRole("button", { name: "授权并生成候选", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "审阅并确认", exact: true }),
    ).toBeVisible({ timeout: 20000 });
    expect(
      (await (await page.request.get(`${origin}/api/v1/memories`)).json())
        .items,
    ).toHaveLength(0);
    const jobs = (
      await (await page.request.get(`${origin}/api/v1/memories/jobs`)).json()
    ).items;
    runs.push(jobs[0].run_id);
    expect(jobs[0].usage.input_tokens).toBe(7);
    expect(jobs[0].usage.output_tokens).toBe(3);
    await page.getByRole("button", { name: "审阅并确认", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog
      .getByRole("textbox", { name: "候选内容", exact: true })
      .fill("项目文档一律使用中文。");
    await dialog
      .getByLabel("记忆使用范围", { exact: true })
      .selectOption("project");
    await dialog.getByLabel("所属项目", { exact: true }).selectOption(project);
    await dialog
      .getByRole("textbox", {
        name: "到期时间（可选，按本机时区）",
        exact: true,
      })
      .fill("2030-01-01T12:00");
    await page.screenshot({ path: "test-results/memories-review.png" });
    await dialog
      .getByRole("button", { name: "确认保存记忆", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await expect(
      page.getByText("来自已确认候选", { exact: false }),
    ).toBeVisible();
    memory = (
      await (await page.request.get(`${origin}/api/v1/memories`)).json()
    ).items[0].id;
    await page.getByRole("button", { name: "新建记忆", exact: true }).click();
    await page
      .getByRole("textbox", { name: "记忆标题", exact: true })
      .fill("重复偏好");
    await page
      .getByRole("textbox", { name: "记忆内容", exact: true })
      .fill("项目文档一律使用中文。");
    await page
      .getByLabel("记忆使用范围", { exact: true })
      .selectOption("project");
    await page.getByLabel("所属项目", { exact: true }).selectOption(project);
    await page.getByRole("button", { name: "保存记忆", exact: true }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "本次没有重复保存" }),
    ).toBeVisible();
    expect(
      (await (await page.request.get(`${origin}/api/v1/memories`)).json())
        .items,
    ).toHaveLength(1);
    await page.getByRole("button", { name: "配置语义召回", exact: true }).click();
    await page
      .getByLabel("记忆向量模型", { exact: true })
      .selectOption(models[1]);
    await page
      .getByRole("checkbox", {
        name: "允许使用所选模型进行语义召回",
        exact: true,
      })
      .check();
    await page
      .getByRole("button", { name: "确认并保存语义配置", exact: true })
      .click();
    await expect(
      page.getByText("已保存。新配置不会自动发送或索引记忆", { exact: false }),
    ).toBeVisible();
    expect(vectors).toBe(0);
    await page
      .getByRole("button", { name: "索引下一批记忆（最多16条）", exact: true })
      .click();
    await expect(
      page.getByRole("status").filter({ hasText: "已索引 1 条" }),
    ).toBeVisible();
    await page
      .getByRole("textbox", { name: "召回预览问题", exact: true })
      .fill("languagechoice");
    await page
      .getByRole("button", { name: "预览记忆召回", exact: true })
      .click();
    const recalled = page.getByLabel("记忆召回结果", { exact: true });
    await expect(recalled).toContainText("没有找到符合范围");
    expect(vectors).toBe(1);
    await page
      .getByLabel("召回上下文", { exact: true })
      .selectOption("project");
    await page.getByLabel("上下文对象", { exact: true }).selectOption(project);
    await page
      .getByRole("button", { name: "预览记忆召回", exact: true })
      .click();
    await expect(recalled).toContainText("语义与关键词融合");
    await expect(recalled).toContainText("项目文档一律使用中文。");
    expect(vectors).toBe(2);
    await page.getByText("向量处理记录与消耗", { exact: false }).click();
    await expect(page.getByLabel("资源处理 Token 用量").first()).toContainText(
      "输入 6 · 输出 0",
    );
    await page.screenshot({
      path: "test-results/memories-desktop.png",
      fullPage: true,
    });
    expect(
      (
        await page.request.delete(
          `${origin}/api/v1/conversations/${conversation}`,
          { headers },
        )
      ).ok(),
    ).toBeTruthy();
    conversation = "";
    await page.reload();
    await page.getByRole("button", { name: "能力中心", exact: true }).click();
    await page.getByRole("button", { name: "长期记忆", exact: true }).click();
    await expect(
      page.getByText("已确认候选（来源已删除）", { exact: false }),
    ).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await page
      .getByRole("heading", { name: "语义召回", exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: "test-results/memories-mobile.png",
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBeTruthy();
    expect(await page.locator(".memory-extraction-form select").evaluateAll((elements) => elements.every((element) => element.getBoundingClientRect().right <= window.innerWidth))).toBeTruthy();
  } finally {
    test.setTimeout(test.info().timeout + 15000);
    writeFileSync(
      resolve("test-results/memory-test-ids.json"),
      JSON.stringify({
        name,
        username,
        user,
        invitation,
        models,
        runs,
        conversation,
        project,
        memory,
      }),
    );
    if (conversation)
      await page.request.delete(
        `${origin}/api/v1/conversations/${conversation}`,
        { headers },
      );
    if (memory)
      await page.request.delete(`${origin}/api/v1/memories/${memory}`, {
        headers,
      });
    if (project)
      await page.request.delete(
        `${origin}/api/v1/conversation-projects/${project}`,
        { headers },
      );
    if (invitation)
      await admin.request.delete(
        `${origin}/api/v1/admin/invitations/${invitation}`,
        { headers },
      );
    for (const model of models)
      await admin.request.delete(`${origin}/api/v1/admin/models/${model}`, {
        headers,
      });
    await admin.close();
    stub.closeAllConnections();
    await new Promise<void>((done) => stub.close(() => done()));
  }
});
