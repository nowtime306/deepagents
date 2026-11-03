import { expect, test } from "@playwright/test";
import { createServer } from "node:http";
import { createHmac } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

test("schedule edit, real worker, signed notice, persistent batch and mobile", async ({
  page,
}) => {
  test.setTimeout(90000);
  page.setDefaultTimeout(15000);
  const origin = "http://localhost:3100",
    headers = { Origin: origin };
  const name = `主动任务页面验收-${Date.now()}`;
  const modelRequests: string[] = [],
    notices: { signature: string; timestamp: string; body: string }[] = [];
  let active = 0,
    peak = 0;
  const stub = createServer(async (request, response) => {
    let raw = "";
    for await (const part of request) raw += part;
    if (request.url === "/notice") {
      notices.push({
        signature: String(request.headers["x-agent-signature"]),
        timestamp: String(request.headers["x-agent-timestamp"]),
        body: raw,
      });
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end("{}");
      return;
    }
    const body = JSON.parse(raw);
    modelRequests.push(body.model);
    active++;
    peak = Math.max(peak, active);
    await new Promise((done) => setTimeout(done, 2200));
    const id = `chatcmpl-auto-${modelRequests.length}`,
      usage = { prompt_tokens: 9, completion_tokens: 3, total_tokens: 12 };
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
              message: { role: "assistant", content: "主动任务本机验收完成" },
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
          delta: { role: "assistant", content: "主动任务本机验收完成" },
          finish_reason: null,
        },
      ]);
      send([{ index: 0, delta: {}, finish_reason: "stop" }]);
      send([], { usage });
      response.end("data: [DONE]\n\n");
    }
    active--;
  });
  await new Promise<void>((done) => stub.listen(0, "127.0.0.1", done));
  const port = (stub.address() as { port: number }).port;
  const credentials = Object.fromEntries(
    readFileSync(resolve("../../services/api/.env.admin"), "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const index = line.indexOf("=");
        return [line.slice(0, index), line.slice(index + 1)];
      }),
  );
  let model = "",
    agent = "",
    schedule = "",
    batch = "",
    channel = "";
  const conversations: string[] = [],
    runs: string[] = [];
  try {
    await page.goto(origin);
    await page
      .getByLabel("账号", { exact: true })
      .fill(credentials.ADMIN_USERNAME);
    await page
      .getByLabel("密码", { exact: true })
      .fill(credentials.ADMIN_PASSWORD);
    await page.getByRole("button", { name: "进入工作台", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "退出登录", exact: true }),
    ).toBeVisible();
    const configured = await page.request.post(
      `${origin}/api/v1/admin/models`,
      {
        headers,
        data: {
          name,
          model: "local-automation-only",
          base_url: `http://127.0.0.1:${port}/v1`,
          api_key: "local-double-only",
          max_output_tokens: 64,
        },
      },
    );
    expect(configured.status()).toBe(201);
    model = (await configured.json()).id;
    const created = await page.request.post(`${origin}/api/v1/agents`, {
      headers,
      data: { name, model_id: model },
    });
    expect(created.status()).toBe(201);
    agent = (await created.json()).id;
    const published = await page.request.post(
      `${origin}/api/v1/agents/${agent}/publish`,
      { headers, data: { revision: 1 } },
    );
    expect(published.status()).toBe(201);
    const version = (await published.json()).id;
    expect(typeof version).toBe("string");
    await page.getByRole("button", { name: "自动任务", exact: true }).click();
    await page.getByRole("button", { name: "通知", exact: true }).click();
    await page
      .getByRole("button", { name: "添加通知通道", exact: true })
      .click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("通道名称", { exact: true }).fill(name);
    await dialog
      .getByLabel("Webhook 地址", { exact: true })
      .fill(`http://127.0.0.1:${port}/notice`);
    await dialog
      .getByLabel("签名密钥", { exact: true })
      .fill("local-browser-signature");
    await dialog
      .getByLabel("明确允许访问内网通知地址", { exact: false })
      .check();
    const channelSaved = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/notification-channels") &&
        r.request().method() === "POST",
    );
    await dialog.getByRole("button", { name: "保存通道", exact: true }).click();
    const channelResponse = await channelSaved;
    expect(channelResponse.status()).toBe(201);
    channel = (await channelResponse.json()).id;
    await expect(dialog).not.toBeVisible();
    expect(notices).toHaveLength(0);
    await page.getByRole("button", { name: "定时任务", exact: true }).click();
    await page.getByRole("button", { name: "新建任务", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("名称", { exact: true }).fill(name);
    await dialog
      .getByRole("combobox", { name: "已发布智能体", exact: true })
      .selectOption(version);
    await dialog
      .getByLabel("任务目标", { exact: true })
      .fill("只访问本机替身，不调用真实模型");
    await dialog.getByLabel("任务时区", { exact: true }).fill("Asia/Shanghai");
    await dialog
      .getByRole("combobox", { name: "错过执行时刻", exact: true })
      .selectOption("once");
    await dialog.getByRole("checkbox", { name, exact: true }).check();
    await dialog
      .getByRole("button", { name: "预览执行时间", exact: true })
      .click();
    await expect(dialog.getByLabel("执行时间预览")).toBeVisible();
    const scheduleSaved = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/automations") &&
        r.request().method() === "POST",
    );
    await dialog.getByRole("button", { name: "保存任务", exact: true }).click();
    const scheduleResponse = await scheduleSaved;
    expect(scheduleResponse.status()).toBe(201);
    schedule = (await scheduleResponse.json()).id;
    await expect(dialog).not.toBeVisible();
    const card = page
      .locator(".automatic-list > article")
      .filter({ has: page.getByRole("heading", { name, exact: true }) });
    await card.getByRole("button", { name: "编辑", exact: true }).click();
    dialog = page.getByRole("dialog");
    const point = new Date(Date.now() - 10000);
    const stamp = new Intl.DateTimeFormat("sv-SE", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .format(point)
      .replace(" ", "T");
    await dialog.getByLabel("首次执行时间", { exact: true }).fill(stamp);
    await dialog.getByRole("button", { name: "保存任务", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await card.getByRole("button", { name: "启用", exact: true }).click();
    await expect
      .poll(
        async () => {
          const result = await page.request.get(
            `${origin}/api/v1/automations/${schedule}/occurrences`,
          );
          const item = (await result.json()).items[0];
          if (item?.target_id && !conversations.includes(item.target_id)) {
            conversations.push(item.target_id);
            runs.push(item.ask_run_id);
          }
          return item?.result_status;
        },
        { timeout: 20000 },
      )
      .toBe("SUCCEEDED");
    await expect.poll(() => notices.length, { timeout: 10000 }).toBe(1);
    expect(notices[0].signature).toBe(
      "sha256=" +
        createHmac("sha256", "local-browser-signature")
          .update(notices[0].timestamp + "." + notices[0].body)
          .digest("hex"),
    );
    expect(notices[0].body).not.toContain("只访问本机");
    await card.getByRole("button", { name: "执行记录", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("已完成");
    await page.getByRole("button", { name: "关闭记录", exact: true }).click();
    await page.getByRole("button", { name: "批量执行", exact: true }).click();
    await page.getByRole("button", { name: "新建批次", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("名称", { exact: true }).fill(name + "-批量");
    await dialog
      .getByRole("combobox", { name: "已发布智能体", exact: true })
      .selectOption(version);
    await dialog
      .getByLabel("各项目标", { exact: false })
      .fill("第一项\n---\n第二项\n---\n第三项");
    const batchSaved = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/batches") && r.request().method() === "POST",
    );
    await dialog
      .getByRole("button", { name: "创建并执行", exact: true })
      .click();
    const batchResponse = await batchSaved;
    expect(batchResponse.status()).toBe(201);
    batch = (await batchResponse.json()).id;
    await expect(dialog).not.toBeVisible();
    await expect
      .poll(
        async () => {
          const result = await page.request.get(`${origin}/api/v1/batches`);
          const row = (await result.json()).items.find(
            (row: { id: string }) => row.id === batch,
          );
          for (const item of row.items)
            if (item.target_id && !conversations.includes(item.target_id))
              conversations.push(item.target_id);
          return row.status;
        },
        { timeout: 30000 },
      )
      .toBe("COMPLETED");
    expect(modelRequests).toHaveLength(4);
    expect(peak).toBeLessThanOrEqual(2);
    expect(peak).toBe(2);
    await page.reload();
    await page.getByRole("button", { name: "自动任务", exact: true }).click();
    await page.getByRole("button", { name: "批量执行", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: name + "-批量", exact: true }),
    ).toBeVisible();
    await page.screenshot({ path: "test-results/automations-desktop.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(391);
    await page.screenshot({ path: "test-results/automations-mobile.png" });
    await page
      .getByRole("button", { name: "通知", exact: false })
      .filter({ hasNotText: "添加" })
      .first()
      .click();
  } finally {
    test.setTimeout(test.info().timeout + 15000);
    writeFileSync(
      "test-results/automation-test-ids.json",
      JSON.stringify({
        name,
        model,
        agent,
        schedule,
        batch,
        channel,
        conversations,
        runs,
      }),
    );
    if (schedule)
      await page.request.delete(`${origin}/api/v1/automations/${schedule}`, {
        headers,
      });
    if (batch)
      await page.request.post(`${origin}/api/v1/batches/${batch}/action`, {
        headers,
        data: { action: "cancel" },
      });
    for (const conversation of conversations)
      await page.request.delete(
        `${origin}/api/v1/conversations/${conversation}`,
        { headers },
      );
    if (agent)
      await page.request.delete(`${origin}/api/v1/agents/${agent}`, {
        headers,
      });
    if (model)
      await page.request.delete(`${origin}/api/v1/admin/models/${model}`, {
        headers,
      });
    if (channel)
      await page.request.delete(
        `${origin}/api/v1/notification-channels/${channel}`,
        { headers },
      );
    stub.closeAllConnections();
    await new Promise<void>((done) => stub.close(() => done()));
  }
});
