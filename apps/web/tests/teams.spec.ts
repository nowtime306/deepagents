import { expect, test } from "@playwright/test";
import { createServer } from "node:http";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

test("actual published team configuration, independent worker, roles and tokens", async ({
  page,
}) => {
  test.setTimeout(60000);
  const origin = "http://localhost:3100";
  const headers = { Origin: origin };
  const stamp = Date.now();
  const name = `团队页面验收-${stamp}`;
  const requests: string[] = [];
  const childTools: string[][] = [];
  const stub = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    const child = body.model === "local-team-child";
    const finished =
      child ||
      body.messages.some(
        (message: { role: string }) => message.role === "tool",
      );
    requests.push(child ? "child" : "lead");
    if (child)
      childTools.push(
        (body.tools || []).map(
          (tool: { function: { name: string } }) => tool.function.name,
        ),
      );
    const usage = child
      ? { prompt_tokens: 10, completion_tokens: 3, total_tokens: 13 }
      : finished
        ? { prompt_tokens: 7, completion_tokens: 2, total_tokens: 9 }
        : { prompt_tokens: 5, completion_tokens: 1, total_tokens: 6 };
    const call = {
      id: "local-team-task",
      type: "function",
      function: {
        name: "task",
        arguments: JSON.stringify({
          subagent_type: "审阅员",
          description: "独立核对本地样例",
        }),
      },
    };
    const content = child ? "成员本机证据" : "团队本机验证完成";
    const id = `chatcmpl-team-${requests.length}`;
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
              finish_reason: finished ? "stop" : "tool_calls",
              message: finished
                ? { role: "assistant", content }
                : { role: "assistant", content: "", tool_calls: [call] },
            },
          ],
        }),
      );
      return;
    }
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
        delta: finished
          ? { role: "assistant", content }
          : { role: "assistant", tool_calls: [{ index: 0, ...call }] },
        finish_reason: null,
      },
    ]);
    send([
      { index: 0, delta: {}, finish_reason: finished ? "stop" : "tool_calls" },
    ]);
    send([], { usage });
    response.end("data: [DONE]\n\n");
  });
  await new Promise<void>((done) => stub.listen(0, "127.0.0.1", done));
  const address = stub.address() as { port: number };
  const values = Object.fromEntries(
    readFileSync(resolve("../../services/api/.env.admin"), "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const index = line.indexOf("=");
        return [line.slice(0, index), line.slice(index + 1)];
      }),
  );
  const models: string[] = [];
  const agents: string[] = [];
  let conversation = "",
    run = "",
    parent = "";
  try {
    await page.goto(origin);
    await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
    await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
    await page.getByRole("button", { name: "进入工作台", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "退出登录", exact: true }),
    ).toBeVisible();
    for (const kind of ["lead", "child"]) {
      const response = await page.request.post(
        `${origin}/api/v1/admin/models`,
        {
          headers,
          data: {
            name: `${name}-${kind}`,
            model: `local-team-${kind}`,
            base_url: `http://127.0.0.1:${address.port}/v1`,
            api_key: "local-double-only",
            kind: "chat",
            provider: "custom",
            protocol: "openai",
            max_output_tokens: 64,
          },
        },
      );
      expect(response.status()).toBe(201);
      models.push((await response.json()).id);
    }
    const childResponse = await page.request.post(`${origin}/api/v1/agents`, {
      headers,
      data: {
        name: `${name}-成员`,
        model_id: models[1],
        instructions: "只读分析本机验收样例",
      },
    });
    expect(childResponse.status()).toBe(201);
    const child = (await childResponse.json()).id;
    agents.push(child);
    const childVersion = await page.request.post(
      `${origin}/api/v1/agents/${child}/publish`,
      { headers, data: { revision: 1 } },
    );
    expect(childVersion.status()).toBe(201);
    const version = (await childVersion.json()).id;
    await page.reload();
    await page.getByRole("button", { name: "智能体管理", exact: true }).click();
    await page.getByRole("button", { name: "新建智能体", exact: true }).click();
    const form = page.locator(".agent-form");
    await form.getByLabel("名称", { exact: true }).fill(name);
    await form.getByLabel("聊天模型", { exact: true }).selectOption(models[0]);
    await form
      .getByRole("button", { name: "添加团队成员", exact: true })
      .click();
    await form.getByLabel("成员 1 角色名", { exact: true }).fill("审阅员");
    await form.getByLabel("成员 1 版本", { exact: true }).selectOption(version);
    await form
      .getByLabel("成员 1 目标", { exact: true })
      .fill("核对独立证据并说明限制");
    const saved = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/agents") &&
        response.request().method() === "POST",
    );
    await form.getByRole("button", { name: "保存智能体", exact: true }).click();
    const stored = await saved;
    expect(stored.status()).toBe(201);
    parent = (await stored.json()).id;
    agents.push(parent);
    const card = page
      .locator(".run-history-card")
      .filter({ hasText: name })
      .filter({ has: page.getByText(name, { exact: true }) });
    await card.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(form.getByLabel("成员 1 角色名", { exact: true })).toHaveValue(
      "审阅员",
    );
    await expect(form.getByLabel("成员 1 版本", { exact: true })).toHaveValue(
      version,
    );
    await page.getByRole("button", { name: "关闭编辑窗口", exact: true }).click();
    const published = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/agents/${parent}/publish`) &&
        response.request().method() === "POST",
    );
    await card
      .getByRole("button", { name: "发布当前修订", exact: true })
      .click();
    const publication = await published;
    expect(publication.status()).toBe(201);
    const parentVersion = (await publication.json()).id;
    await page.reload();
    await page.getByRole("button", { name: "新建对话", exact: true }).click();
    await page.getByLabel("选择智能体", { exact: true }).selectOption(parent);
    await page
      .getByLabel("智能体版本", { exact: true })
      .selectOption(parentVersion);
    await page
      .getByLabel("发送消息", { exact: true })
      .fill("请独立审阅本机样例");
    const submitted = page.waitForResponse(
      (response) =>
        /\/conversations\/[^/]+\/runs$/.test(response.url()) &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "发送", exact: true }).click();
    const started = await submitted;
    expect(started.status()).toBe(202);
    run = (await started.json()).run_id;
    conversation = new URL(started.url()).pathname.split("/").at(-2)!;
    await expect(
      page.getByText("团队本机验证完成", { exact: true }),
    ).toBeVisible({ timeout: 15000 });
    await expect(page.getByLabel("本轮 Token 用量")).toContainText(
      "输入 22 · 输出 6 Token · 3 次调用",
    );
    expect(requests).toEqual(["lead", "child", "lead"]);
    expect(childTools.length).toBe(1);
    expect(
      childTools[0].some((name) =>
        ["task", "execute", "write_file", "edit_file", "delete"].includes(name),
      ),
    ).toBe(false);
    const turn = page.locator(`.chat-turn[data-run-id="${run}"]`);
    await turn.locator(".chat-process > summary").click();
    const diagnostics = turn.locator(".run-diagnostics");
    await diagnostics.locator(":scope > summary").click();
    await expect(diagnostics.getByLabel("团队执行")).toContainText(
      "审阅员 · 已完成",
    );
    await expect(diagnostics.getByLabel("团队执行")).toContainText(
      "输入 10 / 输出 3",
    );
    await page.screenshot({ path: "test-results/team-execution-desktop.png" });
    await page.getByRole("button", { name: "智能体管理", exact: true }).click();
    await card.getByRole("button", { name: "编辑", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await form
      .getByLabel("成员 1 目标", { exact: true })
      .scrollIntoViewIfNeeded();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(391);
    await page.screenshot({ path: "test-results/team-editor-mobile.png" });
  } finally {
    test.setTimeout(test.info().timeout + 15000);
    writeFileSync(
      "test-results/team-test-ids.json",
      JSON.stringify({ models, run, conversation, agents }),
    );
    try {
      if (run)
        await page.request.post(`${origin}/api/v1/runs/${run}/cancel`, {
          headers,
        });
      if (conversation)
        await page.request.delete(
          `${origin}/api/v1/conversations/${conversation}`,
          { headers },
        );
      for (const identifier of [...agents].reverse())
        await page.request.delete(`${origin}/api/v1/agents/${identifier}`, {
          headers,
        });
      for (const identifier of models)
        await page.request.delete(`${origin}/api/v1/admin/models/${identifier}`, {
          headers,
        });
    } finally {
      stub.closeAllConnections();
      await new Promise<void>((done) => stub.close(() => done()));
    }
  }
});
