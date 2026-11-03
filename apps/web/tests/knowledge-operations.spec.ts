import { expect, test } from "@playwright/test";
import { createServer } from "node:http";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

test("document revisions, reviewed OCR, durable experiment comparison and answer feedback", async ({
  page,
}) => {
  test.setTimeout(120000);
  page.setDefaultTimeout(15000);
  const origin = "http://localhost:3100",
    headers = { Origin: origin },
    name = `知识运营页面验收-${Date.now()}`;
  const models: string[] = [],
    runs: string[] = [],
    calls: { path: string; model: string }[] = [];
  let knowledge = "",
    conversation = "";
  const stub = createServer(async (request, response) => {
    let raw = "";
    for await (const part of request) raw += part;
    const body = JSON.parse(raw);
    calls.push({ path: request.url || "", model: body.model });
    if (request.url === "/v1/embeddings") {
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
    if (request.url === "/v1/rerank") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(
        JSON.stringify({
          results: body.documents.map((_: string, index: number) => ({
            index,
            relevance_score: 1 - index / 100,
          })),
          meta: { tokens: { input_tokens: 150, output_tokens: 10 } },
        }),
      );
      return;
    }
    const images = body.messages.some(
      (message: { content: unknown }) =>
        Array.isArray(message.content) &&
        message.content.some(
          (block: { type: string }) => block.type === "image_url",
        ),
    );
    const content = images
      ? JSON.stringify({ pages: [{ page: 1, text: "扫描产品价格200元" }] })
      : "资料已处理，可以评价这条回答。";
    const id = `chatcmpl-knowledge-${Date.now()}`,
      usage = { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 };
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
    for (const kind of ["embedding", "rerank", "vision"]) {
      const configured = await page.request.post(
        `${origin}/api/v1/admin/models`,
        {
          headers,
          data: {
            name: name + "-" + kind,
            model: "local-" + kind,
            base_url: `http://127.0.0.1:${port}/v1`,
            api_key: "local-double-only",
            kind,
            ...(kind === "vision"
              ? { supports_images: true, max_output_tokens: 8192 }
              : {}),
          },
        },
      );
      expect(configured.status()).toBe(201);
      models.push((await configured.json()).id);
    }
    const created = await page.request.post(
      `${origin}/api/v1/capabilities/knowledge`,
      {
        headers,
        data: {
          name,
          embedding_model_id: models[0],
          rerank_model_id: models[1],
        },
      },
    );
    expect(created.status()).toBe(201);
    knowledge = (await created.json()).id;
    await page.getByRole("button", { name: "能力中心", exact: true }).click();
    await page
      .getByRole("article")
      .filter({ has: page.getByRole("heading", { name, exact: true }) })
      .getByRole("button", { name: "管理文档与检索", exact: true })
      .click();
    await page.getByRole("tab", { name: "配置", exact: true }).click();
    await page.getByText("分块策略与处理用量", { exact: true }).click();
    await page.getByLabel("分块字符数", { exact: true }).fill("400");
    await page.getByLabel("重叠字符数", { exact: true }).fill("50");
    await page
      .getByRole("button", { name: "保存分块策略", exact: true })
      .click();
    await expect(
      page.getByRole("status").filter({ hasText: "分块策略已保存" }),
    ).toBeVisible();
    expect(calls).toHaveLength(0);
    const source = {
      name: "产品资料.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("产品价格200元，保修三年。"),
    };
    await page.getByRole("tab", { name: "文档", exact: true }).click();
    await page.getByRole("button", { name: "上传文档", exact: true }).click();
    await page
      .getByLabel("上传知识文档", { exact: true })
      .setInputFiles(source);
    await page.getByRole("button", { name: "开始上传", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "上传文档", exact: true }).getByRole("status").filter({ hasText: "导入" })).toBeVisible();
    await page.getByRole("dialog", { name: "上传文档", exact: true }).getByRole("button", { name: "关闭", exact: true }).click();
    const documentCard = page
      .getByRole("row")
      .filter({ hasText: "产品资料.md" });
    await expect(documentCard).toContainText("向量已就绪");
    const afterFirst = calls.length;
    await page.getByRole("button", { name: "上传文档", exact: true }).click();
    await page
      .getByLabel("上传知识文档", { exact: true })
      .setInputFiles(source);
    await page.getByRole("button", { name: "开始上传", exact: true }).click();
    await expect(
      page.getByRole("dialog", { name: "上传文档", exact: true })
        .getByRole("status")
        .filter({ hasText: "没有重复导入或请求向量模型" }),
    ).toBeVisible();
    expect(calls).toHaveLength(afterFirst);
    await page.getByRole("dialog", { name: "上传文档", exact: true }).getByRole("button", { name: "关闭", exact: true }).click();
    await documentCard
      .getByLabel("更新此文档", { exact: true })
      .setInputFiles({
        ...source,
        buffer: Buffer.from("产品价格300元，保修三年。"),
      });
    await expect(documentCard).toContainText("修订 2");
    await documentCard
      .getByRole("button", { name: "分块与修订历史", exact: true })
      .click();
    let dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("产品价格300元");
    await dialog.getByLabel("文档修订", { exact: true }).selectOption("1");
    await expect(dialog).toContainText("产品价格200元");
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await page.getByRole("button", { name: "OCR导入", exact: true }).click();
    await page.getByText("扫描件与图片 OCR", { exact: true }).click();
    await page
      .getByLabel("OCR识别模型", { exact: true })
      .selectOption(models[2]);
    const encoded = await page.evaluate(() => {
      const canvas = document.createElement("canvas");
      canvas.width = 480;
      canvas.height = 120;
      const context = canvas.getContext("2d")!;
      context.fillStyle = "white";
      context.fillRect(0, 0, 480, 120);
      context.fillStyle = "black";
      context.font = "24px sans-serif";
      context.fillText("INVOICE TOTAL 200", 20, 70);
      return canvas.toDataURL("image/png").split(",")[1];
    });
    await page
      .getByLabel("待识别文件", { exact: true })
      .setInputFiles({
        name: "invoice.png",
        mimeType: "image/png",
        buffer: Buffer.from(encoded, "base64"),
      });
    await page
      .getByRole("button", { name: "授权识别并生成草稿", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "校对识别草稿", exact: true }),
    ).toBeVisible({ timeout: 20000 });
    const jobs = (
      await (
        await page.request.get(
          `${origin}/api/v1/capabilities/knowledge/${knowledge}/ocr`,
        )
      ).json()
    ).items;
    runs.push(jobs[0].run_id);
    expect(jobs[0].usage.input_tokens).toBe(10);
    expect(jobs[0].usage.output_tokens).toBe(5);
    expect(
      (
        await (
          await page.request.get(
            `${origin}/api/v1/capabilities/knowledge/${knowledge}/documents`,
          )
        ).json()
      ).items,
    ).toHaveLength(1);
    await page
      .getByRole("button", { name: "校对识别草稿", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await expect(
      dialog.getByRole("img", { name: "第1页原图", exact: true }),
    ).toBeVisible();
    expect((await dialog.boundingBox())?.width).toBeGreaterThanOrEqual(800);
    await expect.poll(() => dialog
        .getByRole("img", { name: "第1页原图", exact: true })
        .evaluate((image) => (image as HTMLImageElement).naturalWidth)
    ).toBeGreaterThan(0);
    await dialog
      .getByLabel("第1页识别文字", { exact: true })
      .fill("扫描产品价格250元，人工校对完成。");
    await page.screenshot({ path: "test-results/knowledge-ocr-review.png" });
    await dialog
      .getByRole("button", { name: "确认校对并导入", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await page.getByRole("tab", { name: "文档", exact: true }).click();
    await expect(
      page
        .getByRole("row")
        .filter({ hasText: "invoice.png.ocr.txt" }),
    ).toContainText("向量已就绪");
    await page.getByRole("tab", { name: "评测", exact: true }).click();
    await page.getByText("评测题集与检索实验", { exact: true }).click();
    await page.getByText("新建评测题集", { exact: true }).click();
    const datasetName = name + "-题集";
    await page.getByLabel("评测题集名称", { exact: true }).fill(datasetName);
    await page.getByLabel("评测题集来源", { exact: true }).selectOption("synthetic");
    await page.getByLabel("评测问题1", { exact: true }).fill("产品价格");
    let group = page.getByRole("group", { name: "问题 1", exact: true });
    await group.getByRole("checkbox", { name: /产品资料.md/ }).check();
    await page
      .getByRole("button", { name: "添加评测问题", exact: true })
      .click();
    await page.getByLabel("评测问题2", { exact: true }).fill("扫描产品价格");
    await page.getByLabel("评测题型2", { exact: true }).selectOption("scanned");
    group = page.getByRole("group", { name: "问题 2", exact: true });
    await group.getByRole("checkbox", { name: /invoice.png.ocr.txt/ }).check();
    await page
      .getByRole("button", { name: "保存评测题集", exact: true })
      .click();
    await expect(page.locator(".evaluation-datasets")).toContainText(
      datasetName,
    );
    await page
      .getByRole("checkbox", {
        name: "我确认本次检索实验可能产生模型费用，并按此预算执行",
        exact: true,
      })
      .check();
    await page
      .getByRole("button", { name: "开始检索实验", exact: true })
      .click();
    await expect(
      page.locator(".evaluation-experiments article").first(),
    ).toContainText("已完成", { timeout: 20000 });
    await expect(
      page
        .locator(".evaluation-experiments article")
        .first()
        .getByLabel("实验 Token 用量", { exact: true }),
    ).toContainText("输入 312 · 输出 20");
    let experiments = (
      await (
        await page.request.get(
          `${origin}/api/v1/capabilities/knowledge/${knowledge}/experiments`,
        )
      ).json()
    ).items;
    const first = experiments[0].id;
    expect(experiments[0].metrics.complete).toBeTruthy();
    expect(experiments[0].metrics.citation_correctness).toBeNull();
    await page.getByRole("tab", { name: "配置", exact: true }).click();
    await page.getByLabel("最低关键词覆盖率", { exact: true }).fill("40");
    await page
      .getByRole("button", { name: "保存检索策略", exact: true })
      .click();
    await page.getByRole("tab", { name: "评测", exact: true }).click();
    await page.getByText("评测题集与检索实验", { exact: true }).click();
    await page
      .getByRole("checkbox", {
        name: "我确认本次检索实验可能产生模型费用，并按此预算执行",
        exact: true,
      })
      .check();
    await page
      .getByRole("button", { name: "开始检索实验", exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (
            await (
              await page.request.get(
                `${origin}/api/v1/capabilities/knowledge/${knowledge}/experiments`,
              )
            ).json()
          ).items.filter(
            (item: { status: string }) => item.status === "SUCCEEDED",
          ).length,
        { timeout: 20000 },
      )
      .toBe(2);
    experiments = (
      await (
        await page.request.get(
          `${origin}/api/v1/capabilities/knowledge/${knowledge}/experiments`,
        )
      ).json()
    ).items;
    await expect(page.getByText("对比两次实验", { exact: true })).toBeVisible();
    await page.getByText("对比两次实验", { exact: true }).click();
    await page.getByLabel("对照实验一", { exact: true }).selectOption(first);
    await page
      .getByLabel("对照实验二", { exact: true })
      .selectOption(experiments[0].id);
    await expect(
      page.getByText("题集、来源或检索设置有差异", { exact: false }),
    ).toBeVisible();
    await page
      .locator(".evaluation-experiments article")
      .first()
      .getByRole("button", { name: "查看逐题结果", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("产品价格");
    await expect(dialog).toContainText("修订 2");
    await dialog
      .getByRole("button", { name: "关闭逐题结果", exact: true })
      .click();
    await page.screenshot({
      path: "test-results/knowledge-evaluation-desktop.png",
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page
      .getByText("对比两次实验", { exact: true })
      .scrollIntoViewIfNeeded();
    expect(
      await page
        .locator(".knowledge-processing select")
        .evaluateAll((items) =>
          items.every(
            (item) => item.getBoundingClientRect().right <= window.innerWidth,
          ),
        ),
    ).toBeTruthy();
    await page.screenshot({
      path: "test-results/knowledge-evaluation-mobile.png",
    });
    await page.setViewportSize({ width: 1440, height: 900 });
    const createdConversation = await page.request.post(
      `${origin}/api/v1/conversations`,
      { headers, data: { title: name } },
    );
    conversation = (await createdConversation.json()).id;
    const started = await page.request.post(
      `${origin}/api/v1/conversations/${conversation}/runs`,
      {
        headers,
        data: {
          model_id: models[2],
          message: "请给出可评价的回答",
          native_search: false,
        },
      },
    );
    expect(started.status()).toBe(202);
    const run = (await started.json()).run_id;
    runs.push(run);
    await expect
      .poll(
        async () =>
          (
            await (
              await page.request.get(`${origin}/api/v1/runs/${run}`)
            ).json()
          ).status,
        { timeout: 20000 },
      )
      .toBe("SUCCEEDED");
    await page.reload();
    await page.getByRole("button", { name, exact: true }).click();
    await page.getByRole("button", { name: "评价回答", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("回答是否有帮助", { exact: true })
      .selectOption("helpful");
    await dialog
      .getByLabel("引用是否正确", { exact: true })
      .selectOption("true");
    await dialog
      .getByLabel("反馈说明", { exact: true })
      .fill("本机页面验收，已核对示例。");
    await dialog.getByRole("button", { name: "保存反馈", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await page.reload();
    await page.getByRole("button", { name, exact: true }).click();
    await page.getByRole("button", { name: "评价回答", exact: true }).click();
    dialog = page.getByRole("dialog");
    await expect(
      dialog.getByLabel("回答是否有帮助", { exact: true }),
    ).toHaveValue("helpful");
    await expect(dialog.getByLabel("反馈说明", { exact: true })).toHaveValue(
      "本机页面验收，已核对示例。",
    );
    await page.screenshot({ path: "test-results/answer-feedback.png" });
    await dialog.getByRole("button", { name: "清除反馈", exact: true }).click();
    await expect(dialog).not.toBeVisible();
  } finally {
    test.setTimeout(test.info().timeout + 15000);
    if (knowledge) {
      const operations = await page.request.get(
        `${origin}/api/v1/capabilities/knowledge/${knowledge}/operations`,
      );
      if (operations.ok())
        for (const row of (await operations.json()).items) runs.push(row.id);
      const jobs = await page.request.get(
        `${origin}/api/v1/capabilities/knowledge/${knowledge}/ocr`,
      );
      if (jobs.ok())
        for (const row of (await jobs.json()).items) {
          if (row.run_id) runs.push(row.run_id);
          if (["PENDING", "PREPARING"].includes(row.status))
            await page.request.post(
              `${origin}/api/v1/capabilities/knowledge/${knowledge}/ocr/${row.id}/cancel`,
              { headers },
            );
        }
      const experiments = await page.request.get(
        `${origin}/api/v1/capabilities/knowledge/${knowledge}/experiments`,
      );
      if (experiments.ok())
        for (const row of (await experiments.json()).items)
          if (["PENDING", "RUNNING"].includes(row.status))
            await page.request.post(
              `${origin}/api/v1/capabilities/knowledge/${knowledge}/experiments/${row.id}/cancel`,
              { headers },
            );
    }
    writeFileSync(
      resolve("test-results/knowledge-operations-test-ids.json"),
      JSON.stringify({
        name,
        models,
        knowledge,
        conversation,
        runs: [...new Set(runs)],
        calls,
      }),
    );
    if (conversation)
      await page.request.delete(
        `${origin}/api/v1/conversations/${conversation}`,
        { headers },
      );
    if (knowledge)
      await page.request.delete(
        `${origin}/api/v1/capabilities/knowledge/${knowledge}`,
        { headers },
      );
    for (const model of models)
      await page.request.delete(`${origin}/api/v1/admin/models/${model}`, {
        headers,
      });
    stub.closeAllConnections();
    await new Promise<void>((done) => stub.close(() => done()));
  }
});
