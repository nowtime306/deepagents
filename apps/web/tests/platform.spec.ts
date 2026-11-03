import { test, expect } from "@playwright/test";
import { createCanvas, openCanvas } from "./workflow-test-ui";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { decodeFrames } from "../src/lib/api/platform";

test("actual ten model purposes, provider presets, filtering and editing", async ({
  page,
}) => {
  test.setTimeout(90000);
  const path = resolve(process.cwd(), "../../services/api/.env.admin");
  test.skip(!existsSync(path), "Initialize a local admin first");
  const values = Object.fromEntries(
    readFileSync(path, "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const index = line.indexOf("=");
        return [line.slice(0, index), line.slice(index + 1)];
      }),
  );
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const prefix = `多类型验证-${Date.now()}`;
  const headers = { Origin: "http://localhost:3100" };
  const kinds = [
    "chat",
    "embedding",
    "multimodal_embedding",
    "vision",
    "image_generation",
    "video",
    "audio",
    "rerank",
    "speech_to_text",
    "text_to_speech",
  ];
  try {
    for (const kind of kinds) {
      await page
        .getByRole("button", { name: "添加模型", exact: true })
        .first()
        .click();
      const dialog = page.getByRole("dialog");
      await expect(
        dialog.getByLabel("模型类型", { exact: true }).locator("option"),
      ).toHaveCount(10);
      await expect(
        dialog.getByLabel("服务商", { exact: true }).locator("option"),
      ).toHaveCount(13);
      if (kind === "chat") {
        await dialog
          .getByLabel("服务商", { exact: true })
          .selectOption("gemini");
        await expect(
          dialog.getByLabel("服务地址", { exact: false }),
        ).toHaveValue(
          "https://generativelanguage.googleapis.com/v1beta/openai",
        );
        await expect(dialog.getByLabel("接口协议")).toHaveValue("openai");
      }
      await dialog
        .getByLabel("服务商", { exact: true })
        .selectOption(kind === "chat" ? "anthropic" : "siliconflow");
      await expect(dialog.getByLabel("接口协议")).toHaveValue(
        kind === "chat" ? "anthropic" : "openai",
      );
      await dialog.getByLabel("模型类型", { exact: true }).selectOption(kind);
      await dialog
        .getByLabel("显示名称", { exact: true })
        .fill(`${prefix}-${kind}`);
      await dialog.getByLabel("模型名称", { exact: true }).fill(`ui-${kind}`);
      await dialog
        .getByLabel("服务地址", { exact: false })
        .fill("http://127.0.0.1:8793/v1");
      await dialog
        .getByLabel("API 密钥", { exact: true })
        .fill("private-types-test-secret");
      if (kind === "rerank") {
        await expect(
          dialog.getByLabel("设为默认模型", { exact: true }),
        ).toBeDisabled();
        await page.screenshot({
          path: "test-results/model-types-dialog.png",
          fullPage: true,
        });
      }
      await dialog.getByRole("button", { name: "保存模型" }).click();
      await expect(dialog).not.toBeVisible();
    }
    await page.reload();
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.getByLabel("搜索模型", { exact: true }).fill(prefix);
    await expect(page.getByRole("article")).toHaveCount(10);
    await page.getByLabel("筛选服务商").selectOption("anthropic");
    await expect(page.getByRole("article")).toHaveCount(1);
    await expect(
      page
        .getByRole("article")
        .getByText("Anthropic Messages", { exact: true }),
    ).toBeVisible();
    await page.getByLabel("筛选服务商").selectOption("");
    await page.getByLabel("筛选模型类型").selectOption("rerank");
    const card = page.getByRole("article");
    await expect(card).toHaveCount(1);
    await expect(card.getByRole("button", { name: "测试连接" })).toBeEnabled();
    await card.getByRole("button", { name: "编辑", exact: true }).click();
    const edit = page.getByRole("dialog");
    await expect(edit.getByLabel("模型类型", { exact: true })).toHaveValue(
      "rerank",
    );
    await expect(edit.getByLabel("服务商", { exact: true })).toHaveValue(
      "siliconflow",
    );
    await expect(edit.getByLabel("API 密钥", { exact: true })).toHaveValue("");
    await edit
      .getByLabel("显示名称", { exact: true })
      .fill(`${prefix}-重排已编辑`);
    await edit.getByRole("button", { name: "保存模型" }).click();
    await expect(edit).not.toBeVisible();
    await page.getByLabel("搜索模型", { exact: true }).fill(prefix);
    await page.getByLabel("筛选模型类型").selectOption("rerank");
    await expect(
      page.getByRole("article").getByText("密钥已配置", { exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/model-types-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBeTruthy();
    await page.screenshot({
      path: "test-results/model-types-mobile.png",
      fullPage: true,
    });
    const listing = await page.request.get(
      "http://localhost:3100/api/v1/admin/models",
    );
    const body = await listing.json();
    expect(JSON.stringify(body)).not.toContain("private-types-test-secret");
    expect(
      body.items.filter((row: { name: string }) => row.name.startsWith(prefix)),
    ).toHaveLength(10);
  } finally {
    const response = await page.request.get(
      "http://localhost:3100/api/v1/admin/models",
    );
    for (const row of (await response.json()).items || []) {
      if (row.name.startsWith(prefix))
        await page.request.delete(
          `http://localhost:3100/api/v1/admin/models/${row.id}`,
          { headers },
        );
    }
  }
});

test("SSE preserves split frames and ignores heartbeat", () => {
  const first = decodeFrames(
    ': heartbeat\n\ndata: {"event_id":1,"event":"message.delta","data":{"text":"你',
  );
  expect(first.events).toEqual([]);
  const second = decodeFrames(first.rest + '好"}}\n\n');
  expect(second.events[0].data.text).toBe("你好");
  expect(second.rest).toBe("");
});

test("Chinese login, conversation, streamed reply and history", async ({
  page,
}) => {
  let loggedIn = false;
  let created = false;
  let completed = false;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let data: unknown = {};
    if (path === "/auth/identity/providers") data = { items: [] };
    if (path === "/auth/security/pending") data = { mfa_required: false, challenge: "" };
    if (path === "/auth/me") {
      return route.fulfill({
        status: loggedIn ? 200 : 401,
        json: loggedIn
          ? { username: "测试用户" }
          : { error: { message: "请先登录" } },
      });
    }
    if (path === "/auth/login") {
      loggedIn = true;
      data = { username: "测试用户" };
    }
    if (path === "/models")
      data = {
        items: [
          { id: "test", name: "联调模型" },
          { id: "second", name: "备用模型" },
        ],
      };
    if (path === "/conversations") {
      if (route.request().method() === "POST") {
        created = true;
        data = { id: "c1", title: "你好" };
      } else data = { items: created ? [{ id: "c1", title: "你好" }] : [] };
    }
    if (path === "/conversations/c1/runs") data = { run_id: "r1" };
    if (path === "/conversations/c1/references") data = { revision: 0, items: [] };
    if (path === "/conversations/c1/usage") {
      const total = {
        calls: completed ? 1 : 0, reported_calls: completed ? 1 : 0,
        unknown_calls: 0, pending_calls: 0,
        input_tokens: completed ? 90 : null, output_tokens: completed ? 10 : null,
      };
      data = { runs: completed ? [{ run_id: "r1", ...total }] : [], total };
    }
    if (path === "/usage/estimate") data = {
      input_estimate: 805, output_reserve: 4096, first_call_reserve: 4901,
      history_limited: false, turn_token_limit: 64000, note: "本地粗估",
    };
    if (path === "/runs")
      data = {
        items: completed
          ? [
              {
                id: "r1",
                conversation_id: "c1",
                conversation_title: "你好",
                model_id: "test",
                status: "SUCCEEDED",
                created_at: "2026-10-30T00:00:00Z",
                finished_at: "2026-10-30T00:01:00Z",
              },
            ]
          : [],
      };
    if (path === "/conversations/c1/messages")
      data = {
        items: completed
          ? [
              { id: "u1", run_id: "r1", role: "user", content: { text: "你好" } },
              {
                id: "a1",
                run_id: "r1",
                role: "assistant",
                content: { data: { content: "你好，这是测试回答。" } },
              },
            ]
          : [],
        latest_run: completed ? { id: "r1", status: "SUCCEEDED" } : null,
      };
    if (path === "/runs/r1/events") {
      completed = true;
      const events = [
        {
          event_id: 1,
          event: "message.delta",
          data: { text: "你好，这是测试回答。" },
        },
        { event_id: 2, event: "run.completed", data: {} },
      ];
      return route.fulfill({
        contentType: "text/event-stream",
        body: events
          .map((event) => `data: ${JSON.stringify(event)}\n\n`)
          .join(""),
      });
    }
    return route.fulfill({ json: data });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
  await page.screenshot({
    path: "test-results/login-desktop.png",
    fullPage: true,
  });
  await page.getByLabel("账号", { exact: true }).fill("测试用户");
  await page.getByLabel("密码", { exact: true }).fill("test-password");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "今天，我们一起做点什么？" }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/ask-desktop.png",
    fullPage: true,
  });
  await page.getByLabel("发送消息", { exact: true }).fill("你好");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByText("你好，这是测试回答。", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("本轮 Token 用量")).toContainText("输入 90 · 输出 10 Token");
  await page.getByRole("button", { name: "运行记录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "运行记录" })).toBeVisible();
  await expect(
    page.getByRole("article").getByText("已完成", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看对话" }).click();
  await expect(
    page.getByText("你好，这是测试回答。", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "你好", exact: true }).click();
  await expect(
    page.getByText("你好，这是测试回答。", { exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "展开侧边栏" }).click();
  await expect(
    page.getByRole("button", { name: "你好", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "关闭侧边栏" }).click();
  await page.screenshot({
    path: "test-results/ask-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("actual local login and database model management", async ({ page }) => {
  test.setTimeout(60000);
  page.setDefaultTimeout(8000);
  const path = resolve(process.cwd(), "../../services/api/.env.admin");
  test.skip(!existsSync(path), "Initialize a local admin first");
  const values = Object.fromEntries(
    readFileSync(path, "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const index = line.indexOf("=");
        return [line.slice(0, index), line.slice(index + 1)];
      }),
  );
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "今天，我们一起做点什么？" }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/actual-workspace.png",
    fullPage: true,
  });
  const name = `界面验证-${Date.now()}`;
  const agentName = `${name}-智能体`;
  const knowledgeName = `${name}-知识库`;
  const workflowName = `${name}-工作流`;
  let knowledgeId = "";
  const origin = { Origin: "http://localhost:3100" };
  let identifier = "";
  let searchSaved = false;
  await page.route("**/api/v1/search-settings", async (route) => {
    if (route.request().method() === "PUT") searchSaved = true;
    await route.fulfill({
      json: { enabled: searchSaved, key_configured: searchSaved },
    });
  });
  try {
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "模型管理", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "添加模型", exact: true })
      .first()
      .click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("服务商", { exact: true }).selectOption("openai");
    await dialog.getByLabel("显示名称", { exact: true }).fill(name);
    await dialog.getByLabel("模型名称", { exact: true }).fill("ui-test-model");
    await dialog
      .getByLabel("服务地址", { exact: false })
      .fill("http://127.0.0.1:11434/v1");
    await dialog
      .getByLabel("API 密钥", { exact: true })
      .fill("local-test-secret");
    await dialog.getByRole("button", { name: "保存模型" }).click();
    await expect(dialog).not.toBeVisible();
    let card = page
      .getByRole("article")
      .filter({ has: page.getByRole("heading", { name, exact: true }) });
    await expect(card.getByText("密钥已配置", { exact: true })).toBeVisible();
    const saved = await page.request.get(
      "http://localhost:3100/api/v1/admin/models",
    );
    const configuration = (await saved.json()).items.find(
      (model: { name: string }) => model.name === name,
    );
    identifier = configuration.id;
    expect(JSON.stringify(configuration)).not.toContain("local-test-secret");
    const knowledgeResponse = await page.request.post(
      "http://localhost:3100/api/v1/capabilities/knowledge",
      { headers: origin, data: { name: knowledgeName } },
    );
    expect(knowledgeResponse.status()).toBe(201);
    knowledgeId = (await knowledgeResponse.json()).id;
    await page.getByRole("button", { name: "智能体管理", exact: true }).click();
    await page.getByText("联网搜索服务设置", { exact: true }).click();
    await page
      .getByLabel("Tavily API 密钥", { exact: true })
      .fill("fake-search-test-key");
    await page.getByLabel("启用联网搜索服务").check();
    await page.getByRole("button", { name: "保存联网配置" }).click();
    await expect(page.getByText("联网搜索配置已保存")).toBeVisible();
    await page.getByRole("button", { name: "新建智能体", exact: true }).click();
    await page.getByLabel("名称", { exact: true }).fill(agentName);
    await page.getByLabel("聊天模型", { exact: true }).selectOption(identifier);
    await page.getByLabel("系统指令", { exact: true }).fill("优先输出测试结论");
    await page.getByLabel(knowledgeName, { exact: true }).check();
    await page.getByLabel("启用联网搜索", { exact: true }).check();
    await page.getByLabel("联网方式").selectOption("openai_native");
    await page.getByRole("button", { name: "保存智能体" }).click();
    await expect(page.getByText(agentName, { exact: true })).toBeVisible();
    const agents = (
      await (
        await page.request.get("http://localhost:3100/api/v1/agents")
      ).json()
    ).items;
    expect(
      agents.find((agent: { name: string }) => agent.name === agentName)
        .web_enabled,
    ).toBe(true);
    expect(
      agents.find((agent: { name: string }) => agent.name === agentName)
        .web_mode,
    ).toBe("openai_native");
    expect(
      agents.find((agent: { name: string }) => agent.name === agentName)
        .knowledge_ids,
    ).toEqual([knowledgeId]);
    await page.getByRole("button", { name: "智能对话", exact: true }).click();
    await page.getByLabel("选择智能体").selectOption({ label: agentName });
    await expect(page.getByLabel("选择模型")).toHaveValue(identifier);
    await expect(page.getByLabel("选择模型")).toBeDisabled();
    await page.getByLabel("选择智能体").selectOption("");
    await page.getByRole("button", { name: "工作流", exact: true }).click();
    await createCanvas(page, workflowName, identifier, "blank");
    await page.getByRole("button", { name: "流程设置", exact: true }).click();
    await page.getByLabel("模型指令", { exact: true }).fill("整理摘要");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await page.getByRole("button", { name: "测试运行", exact: true }).click();
    await page.getByLabel("测试输入", { exact: true }).fill("测试摘要");
    await page.getByRole("button", { name: "运行测试", exact: true }).click();
    await expect(page.getByRole("region", { name: "节点运行情况", exact: true })).toContainText("测试摘要");
    await page.getByRole("button", { name: "发布版本", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("已发布版本 1");
    await page.getByLabel("运行工作流版本", { exact: true }).selectOption("1");
    await page.getByLabel("测试输入", { exact: true }).fill("发布运行结果");
    await page.getByRole("button", { name: "运行已发布版本", exact: true }).click();
    await expect(page.getByRole("region", { name: "节点运行情况", exact: true })).toContainText("发布运行结果");
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.reload();
    await page.getByRole("button", { name: "设置", exact: true }).click();
    card = page
      .getByRole("article")
      .filter({ has: page.getByRole("heading", { name, exact: true }) });
    await card.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(
      page.getByRole("dialog").getByLabel("API 密钥", { exact: true }),
    ).toHaveValue("");
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "保存模型" })
      .click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await expect(card.getByText("密钥已配置", { exact: true })).toBeVisible();
    await page.screenshot({
      path: "test-results/model-management-desktop.png",
      fullPage: true,
    });
    await page.getByRole("button", { name: "智能对话", exact: true }).click();
    await page.getByLabel("选择模型").selectOption(identifier);
    await expect(page.getByLabel("选择模型")).toHaveValue(identifier);
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: "test-results/model-management-mobile.png",
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBeTruthy();
    await page.setViewportSize({ width: 1440, height: 900 });
  } finally {
    const workflowResponse = await page.request.get(
      "http://localhost:3100/api/v1/workflows",
    );
    const testWorkflow = (await workflowResponse.json()).items?.find(
      (workflow: { name: string }) => workflow.name === workflowName,
    );
    if (testWorkflow)
      await page.request.delete(
        `http://localhost:3100/api/v1/workflows/${testWorkflow.id}`,
        { headers: origin },
      );
    const agentResponse = await page.request.get(
      "http://localhost:3100/api/v1/agents",
    );
    const testAgent = (await agentResponse.json()).items?.find(
      (agent: { name: string }) => agent.name === agentName,
    );
    if (testAgent)
      await page.request.delete(
        `http://localhost:3100/api/v1/agents/${testAgent.id}`,
        { headers: origin },
      );
    if (knowledgeId)
      await page.request.delete(
        `http://localhost:3100/api/v1/capabilities/knowledge/${knowledgeId}`,
        { headers: origin },
      );
    if (!identifier) {
      const response = await page.request.get(
        "http://localhost:3100/api/v1/admin/models",
      );
      identifier =
        (await response.json()).items?.find(
          (model: { name: string }) => model.name === name,
        )?.id || "";
    }
    if (identifier)
      await page.request.delete(
        `http://localhost:3100/api/v1/admin/models/${identifier}`,
        { headers: origin },
      );
  }
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
});

test("actual capability center uploads, retrieval, skill resources and MCP settings", async ({
  page,
}) => {
  const path = resolve(process.cwd(), "../../services/api/.env.admin");
  test.skip(!existsSync(path), "Initialize a local admin first");
  const account = Object.fromEntries(
    readFileSync(path, "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const index = line.indexOf("=");
        return [line.slice(0, index), line.slice(index + 1)];
      }),
  );
  const suffix = Date.now().toString();
  const name = `知识验证-${suffix}`;
  const skill = `skill-${suffix}`;
  const mcp = `MCP验证-${suffix}`;
  const headers = { Origin: "http://localhost:3100" };
  let imageIndex = false;
  let imageUploaded = false;
  await page.route("**/api/v1/capabilities/multimodal-models", (route) =>
    route.fulfill({ json: { items: [{ id: "mm", name: "测试图文向量" }] } }),
  );
  await page.route(
    "**/api/v1/capabilities/knowledge/*/image-index",
    (route) => {
      if (route.request().method() === "POST") {
        imageIndex = true;
        return route.fulfill({ status: 201, json: { ok: true } });
      }
      return route.fulfill({
        json: {
          index: imageIndex
            ? { model_id: "mm", model_name: "测试图文向量", dimensions: 2 }
            : null,
        },
      });
    },
  );
  await page.route("**/api/v1/capabilities/knowledge/*/images", (route) => {
    if (route.request().method() === "POST") {
      imageUploaded = true;
      return route.fulfill({ status: 201, json: { id: "picture" } });
    }
    return route.fulfill({
      json: { items: imageUploaded ? [{ id: "picture", name: "猫.png" }] : [] },
    });
  });
  await page.route("**/api/v1/capabilities/knowledge/*/image-search", (route) =>
    route.fulfill({
      json: { items: [{ id: "picture", name: "猫.png", score: 0.9 }] },
    }),
  );
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(account.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(account.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: "能力中心", exact: true }).click();
  try {
    await page.getByRole("button", { name: "新建知识库", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "新建知识库" });
    await dialog.getByLabel("知识库名称", { exact: true }).fill(name);
    await dialog
      .getByRole("button", { name: "创建知识库", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    const card = page
      .getByRole("article")
      .filter({ has: page.getByRole("heading", { name, exact: true }) });
    await card.getByLabel("绑定到通用助手").check();
    await expect(page.getByRole("status")).toContainText("绑定已更新");
    await card.getByRole("button", { name: "管理文档与检索" }).click();
    await page.getByRole("button", { name: "上传文档", exact: true }).click();
    await page.getByLabel("上传知识文档").setInputFiles({
      name: "产品资料.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("产品价格为 200 元，保修三年。"),
    });
    await page.getByRole("button", { name: "开始上传", exact: true }).click();
    const uploaded = page.getByRole("dialog", { name: "上传文档", exact: true });
    await expect(uploaded.getByRole("status").filter({ hasText: "导入" })).toBeVisible();
    await uploaded.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(page.getByText("产品资料.md", { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "检索测试", exact: true }).click();
    await page.getByLabel("检索问题").fill("产品价格");
    await page.getByRole("button", { name: "检索测试", exact: true }).click();
    await expect(page.locator(".knowledge-results")).toContainText("200 元");
    await page.getByRole("tab", { name: "图片资料", exact: true }).click();
    await page.getByRole("button", { name: "绑定模型", exact: true }).click();
    await page.getByLabel("上传知识图片").setInputFiles({
      name: "猫.png",
      mimeType: "image/png",
      buffer: Buffer.from("image"),
    });
    await expect(
      page.getByRole("heading", { name: "已上传图片" }),
    ).toBeVisible();
    await expect(page.getByText("猫.png", { exact: true })).toBeVisible();
    await page.getByLabel("图片检索问题").fill("猫");
    await page.getByRole("button", { name: "检索图片" }).click();
    await expect(page.getByText("猫.png · 相似度 0.900")).toBeVisible();
    await page.screenshot({
      path: "test-results/knowledge-center.png",
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "Skills 技能", exact: true })
      .click();
    await page.getByLabel("导入技能文件").setInputFiles({
      name: "SKILL.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(
        `---\nname: ${skill}\ndescription: 编写中文报告\n---\n\n# 报告规范\n报告应提供事实依据。\n`,
      ),
    });
    const skillCard = page
      .getByRole("article")
      .filter({ has: page.getByRole("heading", { name: new RegExp(skill) }) });
    await expect(skillCard).toBeVisible();
    await skillCard.getByLabel("绑定到通用助手").check();
    await expect(page.getByRole("status")).toContainText("绑定已更新");
    await skillCard
      .getByRole("button", { name: "查看资源", exact: true })
      .click();
    await expect(page.getByRole("dialog", { name: "技能资源" })).toContainText(
      "报告应提供事实依据",
    );
    await page.getByRole("button", { name: "关闭技能资源" }).click();
    await page.screenshot({
      path: "test-results/skills-center.png",
      fullPage: true,
    });
    await page.getByRole("button", { name: "MCP 工具", exact: true }).click();
    await page.getByRole("button", { name: "添加 MCP", exact: true }).click();
    const mcpDialog = page.getByRole("dialog", { name: "添加 MCP 服务" });
    await mcpDialog.getByLabel("服务名称", { exact: true }).fill(mcp);
    await mcpDialog
      .getByLabel("Streamable HTTP 地址")
      .fill("http://127.0.0.1:8791/mcp");
    await mcpDialog.getByLabel("允许访问内网", { exact: false }).check();
    await mcpDialog
      .getByRole("button", { name: "保存 MCP", exact: true })
      .click();
    await expect(mcpDialog).not.toBeVisible();
    await expect(
      page.getByRole("heading", { name: mcp, exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/mcp-center.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBeTruthy();
    await page.setViewportSize({ width: 1440, height: 900 });
  } finally {
    for (const [type, resourceName] of [
      ["knowledge", name],
      ["skills", skill],
      ["mcp", mcp],
    ]) {
      const response = await page.request.get(
        `http://localhost:3100/api/v1/capabilities/${type}`,
      );
      const rows = (await response.json()).items || [];
      for (const row of rows.filter(
        (item: { name: string }) => item.name === resourceName,
      )) {
        await page.request.delete(
          `http://localhost:3100/api/v1/capabilities/${type}/${row.id}`,
          { headers },
        );
      }
    }
  }
});

// 独立媒体入口按KB-13暂时隐藏；恢复入口时重新启用这两个页面用例。
test.skip("speech page submits the selected model and shows generated audio", async ({
  page,
}) => {
  const path = resolve(process.cwd(), "../../services/api/.env.admin");
  test.skip(!existsSync(path), "Initialize a local admin first");
  const values = Object.fromEntries(
    readFileSync(path, "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const index = line.indexOf("=");
        return [line.slice(0, index), line.slice(index + 1)];
      }),
  );
  let created = false;
  await page.route("**/api/v1/speech/transcription-models", (route) =>
    route.fulfill({ json: { items: [{ id: "asr", name: "测试识别" }] } }),
  );
  await page.route("**/api/v1/speech/transcriptions", (route) =>
    route.fulfill({ json: { text: "识别成功" } }),
  );
  await page.route("**/api/v1/speech/understanding-models", (route) =>
    route.fulfill({ json: { items: [{ id: "audio", name: "测试音频理解" }] } }),
  );
  await page.route("**/api/v1/speech/understanding", (route) =>
    route.fulfill({ json: { text: "音频分析成功" } }),
  );
  await page.route("**/api/v1/speech/models", (route) =>
    route.fulfill({ json: { items: [{ id: "tts", name: "测试语音" }] } }),
  );
  await page.route("**/api/v1/speech", async (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON()).toEqual({
        model_id: "tts",
        text: "你好",
        voice: "alex",
      });
      created = true;
      await route.fulfill({ status: 201, json: { id: "audio-test" } });
    } else {
      await route.fulfill({
        json: {
          items: created
            ? [
                {
                  id: "audio-test",
                  model_name: "测试语音",
                  voice: "alex",
                  input: "你好",
                  created_at: "2026-10-30T00:00:00Z",
                },
              ]
            : [],
        },
      });
    }
  });
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: "语音合成", exact: true }).click();
  await page.getByLabel("音色").fill("alex");
  await page.getByLabel("合成文本").fill("你好");
  await page.getByRole("button", { name: "生成语音" }).click();
  await expect(page.locator("audio")).toHaveAttribute(
    "src",
    "/api/v1/speech/audio-test/audio",
  );
  await expect(page.getByRole("link", { name: "下载" })).toBeVisible();
  await page.getByLabel("音频文件", { exact: true }).setInputFiles({
    name: "test.mp3",
    mimeType: "audio/mpeg",
    buffer: Buffer.from("audio"),
  });
  await page.getByRole("button", { name: "开始识别" }).click();
  await expect(page.getByLabel("识别结果")).toHaveText("识别成功");
  await page.getByLabel("理解音频文件").setInputFiles({
    name: "test.wav",
    mimeType: "audio/wav",
    buffer: Buffer.from("audio"),
  });
  await page.getByLabel("音频问题").fill("听到什么");
  await page.getByRole("button", { name: "分析音频" }).click();
  await expect(page.getByLabel("音频分析结果")).toHaveText("音频分析成功");
});

test.skip("vision page submits a picture and shows the answer", async ({ page }) => {
  const path = resolve(process.cwd(), "../../services/api/.env.admin");
  test.skip(!existsSync(path), "Initialize a local admin first");
  const values = Object.fromEntries(
    readFileSync(path, "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const index = line.indexOf("=");
        return [line.slice(0, index), line.slice(index + 1)];
      }),
  );
  await page.route("**/api/v1/vision/models", (route) =>
    route.fulfill({ json: { items: [{ id: "vision", name: "测试视觉" }] } }),
  );
  await page.route("**/api/v1/vision", (route) =>
    route.fulfill({ json: { text: "分析完成" } }),
  );
  await page.route("**/api/v1/vision/video-models", (route) =>
    route.fulfill({ json: { items: [{ id: "video", name: "测试视频" }] } }),
  );
  await page.route("**/api/v1/vision/video", (route) =>
    route.fulfill({ json: { text: "视频分析成功" } }),
  );
  let generated = false;
  await page.route("**/api/v1/vision/generation-models", (route) =>
    route.fulfill({ json: { items: [{ id: "image", name: "测试生成" }] } }),
  );
  await page.route("**/api/v1/vision/generated", (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON()).toEqual({
        model_id: "image",
        prompt: "一只猫",
      });
      generated = true;
      return route.fulfill({ status: 201, json: { id: "image-test" } });
    }
    return route.fulfill({
      json: {
        items: generated
          ? [{ id: "image-test", model_name: "测试生成", prompt: "一只猫" }]
          : [],
      },
    });
  });
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: "图片理解", exact: true }).click();
  await page.getByLabel("图片文件").setInputFiles({
    name: "image.png",
    mimeType: "image/png",
    buffer: Buffer.from("image"),
  });
  await page.getByLabel("图片问题").fill("是什么");
  await page.getByRole("button", { name: "分析图片" }).click();
  await expect(page.getByLabel("分析结果")).toHaveText("分析完成");
  await page.getByLabel("图片提示词").fill("一只猫");
  await page.getByRole("button", { name: "生成图片" }).click();
  await expect(page.getByRole("img", { name: "一只猫" })).toHaveAttribute(
    "src",
    "/api/v1/vision/generated/image-test/image",
  );
  await page.getByLabel("视频文件").setInputFiles({
    name: "test.mp4",
    mimeType: "video/mp4",
    buffer: Buffer.from("video"),
  });
  await page.getByLabel("视频问题").fill("发生了什么");
  await page.getByRole("button", { name: "分析视频" }).click();
  await expect(page.getByLabel("视频分析结果")).toHaveText("视频分析成功");
});

test("native search citations appear as clickable links in conversation history", async ({
  page,
}) => {
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let data: unknown = {};
    if (path === "/auth/me") data = { username: "测试用户" };
    if (path === "/models" || path === "/agents") data = { items: [] };
    if (path === "/conversations")
      data = { items: [{ id: "c1", title: "新闻" }] };
    if (path === "/conversations/c1/references") data = { revision: 0, items: [] };
    if (path === "/conversations/c1/messages")
      data = {
        items: [
          {
            id: "a1",
            role: "assistant",
            content: {
              data: {
                content: [
                  {
                    type: "text",
                    text: "新闻内容。",
                    annotations: [
                      {
                        type: "url_citation",
                        url: "https://example.org/news",
                        end_index: 4,
                      },
                    ],
                  },
                ],
              },
            },
          },
        ],
        latest_run: null,
      };
    return route.fulfill({ json: data });
  });
  await page.goto("http://localhost:3100");
  await page.getByRole("button", { name: "新闻", exact: true }).click();
  await expect(page.getByRole("link", { name: "来源" })).toHaveAttribute(
    "href",
    "https://example.org/news",
  );
});

test("work task drafts persist and can be edited and deleted", async ({
  page,
}) => {
  const path = resolve(process.cwd(), "../../services/api/.env.admin");
  test.skip(!existsSync(path), "Initialize a local admin first");
  const values = Object.fromEntries(
    readFileSync(path, "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const index = line.indexOf("=");
        return [line.slice(0, index), line.slice(index + 1)];
      }),
  );
  const name = `任务草稿验证-${Date.now()}`;
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: /任务工作台/ }).click();
  try {
    await expect(
      page.getByText(
        "保存任务目标，在执行详情查看进度、审批和成果。",
        {
          exact: false,
        },
      ),
    ).toBeVisible();
    await page.getByRole("button", { name: "新建任务", exact: true }).click();
    await page.getByLabel("任务标题").fill(name);
    await page.getByLabel("任务目标").fill("整理文档");
    await page.getByRole("button", { name: "保存草稿" }).click();
    let card = page.getByRole("article").filter({ hasText: name });
    await expect(card).toContainText("整理文档");
    await card.getByRole("button", { name: "编辑" }).click();
    await page.getByLabel("任务目标").fill("整理并汇总文档");
    await page.getByRole("button", { name: "保存草稿" }).click();
    await expect(card).toContainText("整理并汇总文档");
    await page.reload();
    await page.getByRole("button", { name: /任务工作台/ }).click();
    card = page.getByRole("article").filter({ hasText: name });
    await expect(card).toContainText("整理并汇总文档");
    page.once("dialog", (dialog) => dialog.accept());
    await card.getByRole("button", { name: "删除" }).click();
    await expect(card).toHaveCount(0);
  } finally {
    const rows =
      (
        await (
          await page.request.get("http://localhost:3100/api/v1/work-tasks")
        ).json()
      ).items || [];
    for (const row of rows.filter(
      (item: { title: string }) => item.title === name,
    ))
      await page.request.delete(
        `http://localhost:3100/api/v1/work-tasks/${row.id}`,
        { headers: { Origin: "http://localhost:3100" } },
      );
  }
});

test("work run Chinese progress, approval and download controls", async ({
  page,
}) => {
  const path = resolve(process.cwd(), "../../services/api/.env.admin");
  test.skip(!existsSync(path), "Initialize a local admin first");
  const values = Object.fromEntries(
    readFileSync(path, "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const i = line.indexOf("=");
        return [line.slice(0, i), line.slice(i + 1)];
      }),
  );
  const name = `任务执行界面-${Date.now()}`;
  let identifier = "";
  let status = "WAITING_APPROVAL";
  let decision: string | null = null;
  let readinessReads = 0;
  let allowReady = false;
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: /任务工作台/ }).click();
  try {
    await page.getByRole("button", { name: "新建任务", exact: true }).click();
    await page.getByLabel("任务标题").fill(name);
    await page.getByLabel("任务目标").fill("验证任务执行页面");
    await page.getByRole("button", { name: "保存草稿" }).click();
    const card = page.getByRole("article").filter({ hasText: name });
    await expect(card).toBeVisible();
    const rows = (
      await (
        await page.request.get("http://localhost:3100/api/v1/work-tasks")
      ).json()
    ).items;
    identifier = rows.find((row: { title: string }) => row.title === name).id;
    await page.route("**/api/v1/models", (route) =>
      route.fulfill({
        json: { items: [{ id: "test", name: "界面测试模型" }] },
      }),
    );
    await page.route("**/api/v1/work-capabilities", (route) =>
      route.fulfill({ json: { enabled: ++readinessReads > 2 && allowReady } }),
    );
    await page.route(`**/api/v1/work-tasks/${identifier}/runs`, (route) =>
      route.fulfill({
        json:
          route.request().method() === "POST"
            ? { run_id: "ui-run" }
            : {
                items: [
                  {
                    id: "ui-run",
                    status,
                    created_at: new Date().toISOString(),
                  },
                ],
              },
      }),
    );
    await page.route("**/api/v1/work-runs/ui-run", (route) =>
      route.fulfill({
        json: {
          id: "ui-run",
          status,
          result: status === "COMPLETED" ? { text: "任务验证通过" } : null,
        },
      }),
    );
    await page.route("**/api/v1/work-runs/ui-run/events?*", (route) =>
      route.fulfill({
        json: {
          items: [
            { event_id: 1, event: "step.updated", data: {} },
            {
              event_id: 2,
              event: "plan.updated",
              data: {
                todos: [{ content: "核对输入文件", status: "in_progress" }],
              },
            },
          ],
          next_after: 1,
        },
      }),
    );
    await page.route("**/api/v1/work-runs/ui-run/approvals", (route) =>
      route.fulfill({
        json: {
          items: [
            {
              id: "approval",
              digest: "a".repeat(64),
              decision,
              expires_at: new Date(Date.now() + 600000).toISOString(),
              payload: {
                action_requests: [
                  { name: "execute", args: { command: "python3 answer.py" } },
                ],
              },
            },
          ],
        },
      }),
    );
    await page.route(
      "**/api/v1/work-approvals/approval/decisions",
      async (route) => {
        const body = route.request().postDataJSON();
        expect(body.digest).toBe("a".repeat(64));
        decision = body.decision;
        status = "COMPLETED";
        await route.fulfill({ json: { decision } });
      },
    );
    await page.route("**/api/v1/work-runs/ui-run/recovery", (route) =>
      route.fulfill({ json: { job: null, sessions: [] } }),
    );
    await page.route("**/api/v1/work-runs/ui-run/operations", (route) =>
      route.fulfill({ json: { items: [] } }),
    );
    await page.route("**/api/v1/work-runs/ui-run/artifacts", (route) =>
      route.fulfill({
        json: {
          items:
            status === "COMPLETED"
              ? [{ id: "file", path: "/answer.py", size: 9 }]
              : [],
        },
      }),
    );
    await page.route("**/api/v1/work-artifacts/file/download", (route) =>
      route.fulfill({
        body: "print(42)",
        headers: { "Content-Disposition": 'attachment; filename="answer.py"' },
      }),
    );
    await card.getByRole("button", { name: "执行详情" }).click();
    const panel = page.getByRole("region", { name: "任务执行详情" });
    await expect(panel.getByLabel("执行模型")).toHaveValue("test");
    await expect(panel.getByRole("button", { name: "开始执行", exact: true })).toBeDisabled();
    allowReady = true;
    await expect(panel.getByRole("button", { name: "开始执行", exact: true })).toBeEnabled();
    expect(readinessReads).toBeGreaterThanOrEqual(3);
    await expect(
      panel.getByText("进行中：核对输入文件", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      panel.getByRole("button", { name: "批准此操作" }),
    ).toBeVisible();
    await expect(
      panel.locator("pre").filter({ hasText: "python3 answer.py" }).first(),
    ).toBeVisible();
    await panel.getByRole("button", { name: "批准此操作" }).click();
    await expect(
      panel.getByText("任务验证通过", { exact: true }),
    ).toBeVisible();
    await expect(panel.getByRole("link", { name: "/answer.py" })).toBeVisible();
    const download = page.waitForEvent("download");
    await panel.getByRole("link", { name: "/answer.py" }).click();
    expect((await download).suggestedFilename()).toBe("answer.py");
    await page.screenshot({
      path: "test-results/work-execution.png",
      fullPage: true,
    });
    await panel.getByRole("button", { name: "关闭详情" }).click();
  } finally {
    if (identifier)
      await page.request.delete(
        `http://localhost:3100/api/v1/work-tasks/${identifier}`,
        {
          headers: { Origin: "http://localhost:3100" },
        },
      );
  }
});

test("actual document attachment persists across archive and restore", async ({
  page,
}) => {
  const path = resolve(process.cwd(), "../../services/api/.env.admin");
  test.skip(!existsSync(path), "Initialize a local admin first");
  const values = Object.fromEntries(
    readFileSync(path, "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const i = line.indexOf("=");
        return [line.slice(0, i), line.slice(i + 1)];
      }),
  );
  const name = `附件验收-${Date.now()}.txt`;
  let identifier = "";
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  try {
    await page.getByLabel("上传图片或文件", { exact: true }).setInputFiles({
      name,
      mimeType: "text/plain",
      buffer: Buffer.from("附件原始内容：这是浏览器实际上传验证。", "utf8"),
    });
    await expect(
      page.locator(".pending-attachments").getByText(name, { exact: true }),
    ).toBeVisible();
    const rows = (
      await (
        await page.request.get("http://localhost:3100/api/v1/conversations")
      ).json()
    ).items;
    identifier = rows.find((row: { title: string }) => row.title === name).id;
    const files = (
      await (
        await page.request.get(
          `http://localhost:3100/api/v1/conversations/${identifier}/attachments`,
        )
      ).json()
    ).items;
    const downloaded = await page.request.get(
      `http://localhost:3100/api/v1/attachments/${files[0].id}/download`,
    );
    expect(await downloaded.text()).toContain("附件原始内容");
    await page.reload();
    await page.getByRole("button", { name, exact: true }).click();
    await expect(
      page.locator(".pending-attachments").getByText(name, { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: `对话操作 ${name}`, exact: true }).click();
    await page
      .getByRole("button", { name: `归档 ${name}`, exact: true })
      .click();
    await page.getByRole("button", { name: "查看已归档", exact: true }).click();
    await page.getByRole("button", { name, exact: true }).click();
    await expect(
      page.getByText("此对话已归档，恢复后可继续发送消息。", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: `对话操作 ${name}`, exact: true }).click();
    await page
      .getByRole("button", { name: `恢复 ${name}`, exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: name, exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: name, exact: true }).click();
    await expect(
      page.locator(".pending-attachments").getByText(name, { exact: true }),
    ).toBeVisible();
  } finally {
    if (!identifier) {
      const recent =
        (
          await (
            await page.request.get("http://localhost:3100/api/v1/conversations")
          ).json()
        ).items || [];
      identifier =
        recent.find((item: { title: string }) => item.title === name)?.id || "";
    }
    if (identifier)
      await page.request.delete(
        `http://localhost:3100/api/v1/conversations/${identifier}`,
        { headers: { Origin: "http://localhost:3100" } },
      );
  }
});

test("actual workflow canvas saves, executes transforms and publishes a tested version", async ({ page }) => {
  const values = Object.fromEntries(readFileSync(resolve("../../services/api/.env.admin"), "utf8").trim().split(/\r?\n/).map((line) => { const index = line.indexOf("="); return [line.slice(0,index), line.slice(index+1)]; }));
  const name = `画布实际验收-${Date.now()}`; let identifier = "";
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: "工作流", exact: true }).click();
  try {
    identifier = await createCanvas(page, name);
    await page.getByLabel("节点类型", { exact: true }).selectOption("transform");
    await page.getByLabel("文本转换方式", { exact: true }).selectOption("upper");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("草稿已保存");
    await expect(page.getByRole("button", { name: "发布版本", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "测试运行", exact: true }).click();
    await page.getByLabel("测试输入", { exact: true }).fill("hello canvas");
    await page.getByRole("button", { name: "运行测试", exact: true }).click();
    await expect(page.getByRole("region", { name: "节点运行情况", exact: true })).toContainText("HELLO CANVAS");
    await openCanvas(page, name);
    await expect(page.getByLabel("节点类型", { exact: true })).toHaveValue("transform");
    await expect(page.getByLabel("文本转换方式", { exact: true })).toHaveValue("upper");
    const node = page.locator('.react-flow__node[data-id="llm"]');
    const box = (await node.boundingBox())!;
    await page.mouse.move(box.x + 70, box.y + 22); await page.mouse.down();
    await page.mouse.move(box.x + 115, box.y + 57, { steps: 8 }); await page.mouse.up();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("草稿已保存");
    const saved = (await (await page.request.get("http://localhost:3100/api/v1/workflows")).json()).items.find((row: { id: string }) => row.id === identifier);
    expect(saved.revision).toBe(3); expect(saved.tested_revision).toBe(3); expect(saved.layout.llm).toBeTruthy();
    await page.getByRole("button", { name: "发布版本", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("已发布版本 1");
    await page.getByRole("button", { name: "测试运行", exact: true }).click();
    await page.getByLabel("运行工作流版本", { exact: true }).selectOption("1");
    await page.getByRole("button", { name: "运行已发布版本", exact: true }).click();
    const trace = page.getByRole("region", { name: "节点运行情况", exact: true });
    await expect(trace).toContainText("HELLO CANVAS");
    await expect(trace.locator('[data-node="llm"]')).toContainText("已完成");
    await page.getByRole("button", { name: "运行历史", exact: true }).click();
    await page.getByRole("button", { name: "下线版本", exact: true }).click();
    await page.getByRole("button", { name: "测试运行", exact: true }).click();
    await expect(page.getByRole("button", { name: "运行已发布版本", exact: true })).toBeDisabled();
    await page.screenshot({ path: "test-results/workflow-canvas.png", fullPage: true });
  } finally { if (identifier) await page.request.delete(`http://localhost:3100/api/v1/workflows/${identifier}`, { headers: { Origin: "http://localhost:3100" } }); }
});

test("actual private memory management and version scoped API publication", async ({
  page,
}) => {
  test.setTimeout(60000);
  page.setDefaultTimeout(8000);
  const values = Object.fromEntries(
    readFileSync(
      resolve(process.cwd(), "../../services/api/.env.admin"),
      "utf8",
    )
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const i = line.indexOf("=");
        return [line.slice(0, i), line.slice(i + 1)];
      }),
  );
  const name = `记忆发布验收-${Date.now()}`;
  const origin = { Origin: "http://localhost:3100" };
  let memory = "",
    workflow = "",
    keyId = "";
  let oldBinding: Record<string, unknown> | null = null;
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  try {
    oldBinding = await (
      await page.request.get(
        "http://localhost:3100/api/v1/capabilities/bindings",
      )
    ).json();
    await page.getByRole("button", { name: "能力中心", exact: true }).click();
    await page.getByRole("button", { name: "长期记忆", exact: true }).click();
    await page.getByRole("button", { name: "新建记忆", exact: true }).click();
    await page.getByLabel("记忆标题", { exact: true }).fill(name);
    await page
      .getByLabel("记忆内容", { exact: true })
      .fill(`${name}：项目文档优先使用中文，重要决定附验证证据。`);
    await page.getByRole("button", { name: "保存记忆", exact: true }).click();
    const card = page.getByRole("article").filter({ hasText: name });
    await expect(card).toBeVisible();
    const memories = await (
      await page.request.get(
        `http://localhost:3100/api/v1/memories?query=${encodeURIComponent(name)}`,
      )
    ).json();
    memory = memories.items[0].id;
    await card.getByRole("button", { name: "编辑记忆", exact: true }).click();
    await page
      .getByLabel("记忆内容", { exact: true })
      .fill(`${name}：项目文档使用中文；已编辑。`);
    await page.getByRole("button", { name: "保存记忆", exact: true }).click();
    await expect(card).toContainText("已编辑");
    await page
      .getByLabel("允许通用助手读取我的长期记忆", { exact: true })
      .check();
    await expect
      .poll(
        async () =>
          (
            await (
              await page.request.get(
                "http://localhost:3100/api/v1/capabilities/bindings",
              )
            ).json()
          ).memory_enabled,
      )
      .toBe(true);
    await page.reload();
    await page.getByRole("button", { name: "能力中心", exact: true }).click();
    await page.getByRole("button", { name: "长期记忆", exact: true }).click();
    await expect(
      page.getByRole("article").filter({ hasText: name }),
    ).toContainText("已编辑");
    const models = await (
      await page.request.get("http://localhost:3100/api/v1/models")
    ).json();
    const model = models.items[0];
    const definition = {
      nodes: [
        { id: "start", kind: "start" },
        { id: "format", kind: "transform", parameters: { operation: "upper" } },
        { id: "end", kind: "end", input: "format" },
      ],
      edges: [
        { source: "start", target: "format" },
        { source: "format", target: "end" },
      ],
    };
    const created = await page.request.post(
      "http://localhost:3100/api/v1/workflows",
      {
        headers: origin,
        data: { name, model_id: model.id, definition },
      },
    );
    expect(created.status()).toBe(201);
    workflow = (await created.json()).id;
    expect(
      (
        await page.request.post(
          `http://localhost:3100/api/v1/workflows/${workflow}/test-runs`,
          {
            headers: origin,
            data: { text: "publish" },
          },
        )
      ).ok(),
    ).toBe(true);
    expect(
      (
        await page.request.post(
          `http://localhost:3100/api/v1/workflows/${workflow}/publish`,
          {
            headers: origin,
            data: { revision: 1 },
          },
        )
      ).ok(),
    ).toBe(true);
    const versions = await (
      await page.request.get(
        `http://localhost:3100/api/v1/workflows/${workflow}/versions`,
      )
    ).json();
    await page.getByRole("button", { name: "工作流", exact: true }).click();
    await page
      .getByRole("button", { name: "API 发布管理", exact: true })
      .click();
    await page.getByRole("button", { name: "新增 API Key", exact: true }).click();
    await page.getByLabel("调用方名称", { exact: true }).fill(name);
    await page
      .getByLabel("授权发布版本", { exact: true })
      .selectOption(versions.items[0].id);
    await page
      .getByRole("button", { name: "生成调用 Key", exact: true })
      .click();
    const key = await page
      .getByLabel("新生成的 API Key", { exact: true })
      .inputValue();
    expect(key).toMatch(/^oa_/);
    const keys = await (
      await page.request.get("http://localhost:3100/api/v1/api-keys")
    ).json();
    keyId = keys.items.find((item: { name: string }) => item.name === name).id;
    const headers = {
      Authorization: `Bearer ${key}`,
      "Idempotency-Key": encodeURIComponent(name),
    };
    const result = await page.request.post(
      "http://localhost:3100/api/public/v1/workflow-runs",
      {
        headers,
        data: { text: "published output" },
      },
    );
    expect(result.status()).toBe(202);
    const runId = (await result.json()).id;
    await expect
      .poll(
        async () =>
          (
            await (
              await page.request.get(
                `http://localhost:3100/api/public/v1/workflow-runs/${runId}`,
                {
                  headers,
                },
              )
            ).json()
          ).status,
      )
      .toBe("SUCCEEDED");
    const completed = await (
      await page.request.get(
        `http://localhost:3100/api/public/v1/workflow-runs/${runId}`,
        {
          headers,
        },
      )
    ).json();
    expect(completed.output).toBe("PUBLISHED OUTPUT");
    await page
      .getByRole("button", { name: "我已保存，隐藏 Key", exact: true })
      .click();
    await expect(
      page.getByLabel("新生成的 API Key", { exact: true }),
    ).toHaveCount(0);
    await page
      .getByRole("button", { name: "刷新调用记录", exact: true })
      .click();
    await expect(page.getByText(runId, { exact: false })).toBeVisible();
    const keyCard = page
      .getByRole("article")
      .filter({ hasText: name })
      .filter({ hasText: "尾号" });
    page.once("dialog", (dialog) => dialog.accept());
    await keyCard
      .getByRole("button", { name: "撤销 Key", exact: true })
      .click();
    await expect(keyCard).toContainText("已撤销");
    expect(
      (
        await page.request.get(
          `http://localhost:3100/api/public/v1/workflow-runs/${runId}`,
          {
            headers,
          },
        )
      ).status(),
    ).toBe(401);
    await page.screenshot({
      path: "test-results/workflow-publication.png",
      fullPage: true,
    });
  } finally {
    test.setTimeout(90000);
    if (oldBinding)
      await page.request.put(
        "http://localhost:3100/api/v1/capabilities/bindings",
        {
          headers: origin,
          data: oldBinding,
        },
      );
    if (memory)
      await page.request.delete(
        `http://localhost:3100/api/v1/memories/${memory}`,
        {
          headers: origin,
        },
      );
    if (keyId)
      await page.request.delete(
        `http://localhost:3100/api/v1/api-keys/${keyId}`,
        {
          headers: origin,
        },
      );
    if (workflow)
      await page.request.delete(
        `http://localhost:3100/api/v1/workflows/${workflow}`,
        {
          headers: origin,
        },
      );
  }
});

test("actual task input files, empty organization and operations pages", async ({
  page,
}) => {
  test.setTimeout(90000);
  const values = Object.fromEntries(
    readFileSync(
      resolve(process.cwd(), "../../services/api/.env.admin"),
      "utf8",
    )
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const i = line.indexOf("=");
        return [line.slice(0, i), line.slice(i + 1)];
      }),
  );
  const name = `文件组织验收-${Date.now()}`,
    headers = { Origin: "http://localhost:3100" };
  let task = "",
    organization = "";
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  try {
    const created = await page.request.post(
      "http://localhost:3100/api/v1/work-tasks",
      { headers, data: { title: name, goal: "只验证文件页面，不执行模型" } },
    );
    expect(created.status()).toBe(201);
    task = (await created.json()).id;
    await page.getByRole("button", { name: "任务工作台", exact: true }).click();
    const card = page.getByRole("article").filter({ hasText: name });
    await card.getByRole("button", { name: "执行详情", exact: true }).click();
    const panel = page.getByRole("region", { name: "任务执行详情" });
    await panel.getByText(/任务输入文件 ·/).click();
    await panel
      .getByLabel("工作区目标路径", { exact: true })
      .fill("/src/input.py");
    await panel.getByLabel("上传任务文件", { exact: true }).setInputFiles({
      name: "input.py",
      mimeType: "text/plain",
      buffer: Buffer.from("print('source file')"),
    });
    await expect(
      panel.getByRole("link", { name: "/src/input.py", exact: true }),
    ).toBeVisible();
    const files = await (
      await page.request.get(
        `http://localhost:3100/api/v1/work-tasks/${task}/files`,
      )
    ).json();
    expect(files.revision).toBe(2);
    expect(files.items).toHaveLength(1);
    const content = await page.request.get(
      `http://localhost:3100/api/v1/work-input-files/${files.items[0].id}/download`,
    );
    expect(await content.text()).toBe("print('source file')");
    await panel.getByRole("button", { name: "关闭详情", exact: true }).click();
    await card.getByRole("button", { name: "执行详情", exact: true }).click();
    await panel.getByText(/任务输入文件 ·/).click();
    await expect(
      panel.getByRole("link", { name: "/src/input.py", exact: true }),
    ).toBeVisible();
    await panel
      .getByRole("button", { name: "移除输入文件", exact: true })
      .click();
    await expect(
      panel.getByRole("link", { name: "/src/input.py", exact: true }),
    ).toHaveCount(0);
    await panel.getByRole("button", { name: "关闭详情", exact: true }).click();
    await page.getByRole("button", { name: "组织管理", exact: true }).click();
    await page.getByRole("button", { name: "新建组织", exact: true }).click();
    await page.getByLabel("组织名称", { exact: true }).fill(name);
    await page.getByRole("button", { name: "创建组织", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: `${name} · 所有者`, exact: true }),
    ).toBeVisible();
    const organizations = await (
      await page.request.get("http://localhost:3100/api/v1/organizations")
    ).json();
    organization = organizations.items.find(
      (item: { name: string }) => item.name === name,
    ).id;
    await page.getByRole("button", { name: "添加成员", exact: true }).click();
    await expect(page.getByLabel("成员角色", { exact: true })).toHaveValue(
      "viewer",
    );
    await page.getByRole("button", { name: "关闭编辑窗口", exact: true }).click();
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "删除空组织", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: `${name} · 所有者`, exact: true }),
    ).toHaveCount(0);
    organization = "";
    await page.getByRole("button", { name: "运营管理", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "累计用量与估算", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "操作审计", exact: true }),
    ).toBeVisible();
    await page.getByText("设置模型估算单价", { exact: true }).click();
    await expect(page.getByLabel("计价模型", { exact: true })).toBeVisible();
    await page.screenshot({
      path: "test-results/operations-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(
      page.getByRole("heading", { name: "运营管理", exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(392);
    await page.screenshot({
      path: "test-results/operations-mobile.png",
      fullPage: true,
    });
  } finally {
    if (task)
      await page.request.delete(
        `http://localhost:3100/api/v1/work-tasks/${task}`,
        { headers },
      );
    if (organization)
      await page.request.delete(
        `http://localhost:3100/api/v1/organizations/${organization}`,
        { headers },
      );
  }
});

test("actual Agent release pins a version in a document conversation", async ({
  page,
}) => {
  const values = Object.fromEntries(
    readFileSync(
      resolve(process.cwd(), "../../services/api/.env.admin"),
      "utf8",
    )
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const i = line.indexOf("=");
        return [line.slice(0, i), line.slice(i + 1)];
      }),
  );
  const headers = { Origin: "http://localhost:3100" };
  const name = `版本发布验收-${Date.now()}`;
  let agent = "",
    conversation = "";
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  try {
    const models = (
      await (
        await page.request.get("http://localhost:3100/api/v1/models")
      ).json()
    ).items;
    const created = await page.request.post(
      "http://localhost:3100/api/v1/agents",
      {
        headers,
        data: {
          name,
          model_id: models[0].id,
          instructions: "已发布的固定指令",
        },
      },
    );
    expect(created.status()).toBe(201);
    agent = (await created.json()).id;
    await page.reload();
    await page.getByRole("button", { name: "智能体管理", exact: true }).click();
    const card = page
      .getByRole("article")
      .filter({ has: page.getByText(name, { exact: true }) });
    await card
      .getByRole("button", { name: "发布当前修订", exact: true })
      .click();
    await expect(
      card.getByText("智能体 v1 · 已发布", { exact: true }),
    ).toBeVisible();
    await card.getByText("查看已发布指令", { exact: true }).click();
    await expect(
      card.getByText("已发布的固定指令", { exact: true }),
    ).toBeVisible();
    const version = (
      await (
        await page.request.get(
          `http://localhost:3100/api/v1/agents/${agent}/versions`,
        )
      ).json()
    ).items[0].id;
    await page.getByRole("button", { name: "智能对话", exact: true }).click();
    await page.getByLabel("选择智能体").selectOption(agent);
    await page.getByLabel("智能体版本", { exact: true }).selectOption(version);
    await page.getByLabel("上传图片或文件", { exact: true }).setInputFiles({
      name: name + ".txt",
      mimeType: "text/plain",
      buffer: Buffer.from("版本固定附件"),
    });
    await expect(
      page
        .locator(".pending-attachments")
        .getByText(name + ".txt", { exact: true }),
    ).toBeVisible();
    const rows = (
      await (
        await page.request.get("http://localhost:3100/api/v1/conversations")
      ).json()
    ).items;
    const row = rows.find(
      (item: { title: string }) => item.title === (name + ".txt").slice(0, 32),
    );
    conversation = row.id;
    expect(row.agent_version_id).toBe(version);
    await page.reload();
    await page.getByRole("button", { name: row.title, exact: true }).click();
    await expect(page.getByLabel("智能体版本", { exact: true })).toHaveValue(
      version,
    );
    await page.screenshot({
      path: "test-results/agent-version-selection.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByLabel("智能体版本", { exact: true })).toHaveValue(
      version,
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: "test-results/agent-version-mobile.png",
      fullPage: true,
    });
  } finally {
    if (conversation)
      await page.request.delete(
        `http://localhost:3100/api/v1/conversations/${conversation}`,
        { headers },
      );
    if (agent)
      await page.request.delete(
        `http://localhost:3100/api/v1/agents/${agent}`,
        { headers },
      );
  }
});

test("actual chat image upload, native search preference and refreshed message preview", async ({
  page,
}) => {
  const values = Object.fromEntries(
    readFileSync(
      resolve(process.cwd(), "../../services/api/.env.admin"),
      "utf8",
    )
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const i = line.indexOf("=");
        return [line.slice(0, i), line.slice(i + 1)];
      }),
  );
  const headers = { Origin: "http://localhost:3100" };
  const name = `图片问答验收-${Date.now()}.png`;
  let identifier = "";
  await page.goto("http://localhost:3100");
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  try {
    const catalog = await (
      await page.request.get("http://localhost:3100/api/v1/models")
    ).json();
    const model = catalog.items.find(
      (item: { supports_images?: boolean; native_search_mode?: string }) =>
        item.supports_images && item.native_search_mode,
    );
    expect(model).toBeTruthy();
    await page.getByLabel("选择模型").selectOption(model.id);
    await expect(
      page.getByRole("button", { name: "联网搜索", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "联网搜索", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "联网搜索", exact: true }),
    ).toHaveAttribute("aria-pressed", "false");
    await page.getByRole("button", { name: "联网搜索", exact: true }).click();
    const encoded = await page.evaluate(() => {
      const c = document.createElement("canvas");
      c.width = 240;
      c.height = 160;
      const ctx = c.getContext("2d")!;
      ctx.fillStyle = "#e5edf5";
      ctx.fillRect(0, 0, 240, 160);
      ctx.fillStyle = "#90b1cf";
      ctx.fillRect(28, 28, 72, 70);
      ctx.fillStyle = "#c4d5e4";
      ctx.fillRect(112, 28, 98, 20);
      ctx.fillRect(112, 60, 78, 12);
      ctx.fillRect(28, 118, 182, 12);
      return c.toDataURL("image/png").split(",")[1];
    });
    await page.getByLabel("上传图片或文件", { exact: true }).setInputFiles({
      name,
      mimeType: "image/png",
      buffer: Buffer.from(encoded, "base64"),
    });
    await expect(
      page
        .locator(".pending-attachments")
        .getByRole("img", { name, exact: true }),
    ).toBeVisible();
    const rows = (
      await (
        await page.request.get("http://localhost:3100/api/v1/conversations")
      ).json()
    ).items;
    identifier = rows.find(
      (item: { title: string }) => item.title === name.slice(0, 32),
    ).id;
    const attachment = (
      await (
        await page.request.get(
          `http://localhost:3100/api/v1/conversations/${identifier}/attachments`,
        )
      ).json()
    ).items[0];
    expect(attachment.media_type).toBe("image/png");
    const thumbnail = page
      .locator(".pending-attachments")
      .getByRole("img", { name, exact: true });
    await expect
      .poll(() =>
        thumbnail.evaluate((image) => (image as HTMLImageElement).naturalWidth),
      )
      .toBeGreaterThan(0);
    await page
      .getByLabel("发送消息", { exact: true })
      .evaluate((element, encoded) => {
        const bytes = Uint8Array.from(atob(encoded), (character) =>
          character.charCodeAt(0),
        );
        const transfer = new DataTransfer();
        transfer.items.add(
          new File([bytes], "粘贴验证.png", { type: "image/png" }),
        );
        element.dispatchEvent(
          new ClipboardEvent("paste", {
            clipboardData: transfer,
            bubbles: true,
            cancelable: true,
          }),
        );
      }, encoded);
    await expect(
      page
        .locator(".pending-attachments")
        .getByRole("img", { name: "粘贴验证.png", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "移除附件 粘贴验证.png", exact: true })
      .click();
    await expect(
      page
        .locator(".pending-attachments")
        .getByRole("img", { name: "粘贴验证.png", exact: true }),
    ).toHaveCount(0);

    const preview = await page.request.get(
      `http://localhost:3100/api/v1/attachments/${attachment.id}/download?inline=true`,
    );
    expect(preview.headers()["content-type"]).toBe("image/png");
    await page.reload();
    await page
      .getByRole("button", { name: name.slice(0, 32), exact: true })
      .click();
    await expect(
      page
        .locator(".pending-attachments")
        .getByRole("img", { name, exact: true }),
    ).toBeVisible();
    await page.getByLabel("选择模型", { exact: true }).selectOption(model.id);
    const answer = "图片已收到。这是一张图片问答界面验收用图。";
    let complete = false;
    await page.route(
      `**/api/v1/conversations/${identifier}/runs`,
      async (route) => {
        const body = route.request().postDataJSON();
        expect(body.native_search).toBe(true);
        expect(body.attachment_ids).toEqual([attachment.id]);
        await route.fulfill({ status: 202, json: { run_id: "image-ui-run" } });
      },
    );
    await page.route("**/api/v1/runs/image-ui-run/events?*", async (route) => {
      complete = true;
      await route.fulfill({
        contentType: "text/event-stream",
        body: [
          { event_id: 1, event: "message.delta", data: { text: answer } },
          { event_id: 2, event: "run.completed", data: {} },
        ]
          .map((event) => `data: ${JSON.stringify(event)}\n\n`)
          .join(""),
      });
    });
    await page.route(
      `**/api/v1/conversations/${identifier}/messages`,
      async (route) => {
        await route.fulfill({
          json: {
            items: complete
              ? [
                  {
                    id: "image-user",
                    role: "user",
                    content: {
                      text: "这张图是什么？",
                      attachments: [attachment],
                    },
                  },
                  {
                    id: "image-assistant",
                    role: "assistant",
                    content: { text: answer },
                  },
                ]
              : [],
            latest_run: complete
              ? { id: "image-ui-run", status: "SUCCEEDED" }
              : null,
            pending_attachments: complete ? [] : [attachment],
            native_search: true,
          },
        });
      },
    );
    await page.getByLabel("发送消息", { exact: true }).fill("这张图是什么？");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.getByText(answer, { exact: true })).toBeVisible();
    await expect(
      page
        .locator(".message-attachments")
        .getByRole("img", { name, exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/chat-refresh-desktop.png",
      fullPage: true,
    });
    await page.getByRole("button", { name: "收起侧边栏", exact: true }).click();
    await expect(page.locator(".platform-sidebar")).toHaveCSS("width", "72px");
    await page.getByRole("button", { name: "展开侧边栏", exact: true }).click();
    await page.setViewportSize({ width: 1280, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: "test-results/chat-refresh-mobile.png",
      fullPage: true,
    });
    await page.reload();
    await page.getByRole("button", { name: "展开侧边栏", exact: true }).click();
    await page
      .getByRole("button", { name: name.slice(0, 32), exact: true })
      .click();
    await expect(
      page
        .locator(".message-attachments")
        .getByRole("img", { name, exact: true }),
    ).toBeVisible();
  } finally {
    if (identifier)
      await page.request.delete(
        `http://localhost:3100/api/v1/conversations/${identifier}`,
        { headers },
      );
  }
});


test("actual local Harness loop, draft estimate and persistent per-turn tokens", async ({ page }) => {
  test.setTimeout(90000);
  const { createServer } = await import("node:http");
  const { writeFileSync } = await import("node:fs");
  let calls = 0;
  const requestedLimits: number[] = [];
  const stub = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    requestedLimits.push(body.max_tokens ?? body.max_completion_tokens ?? body.max_output_tokens);
    const finished = body.messages.at(-1)?.role === "tool";
    const id = `chatcmpl-local-${++calls}`;
    const toolCall = { id: `call-${calls}`, type: "function", function: {
      name: "text_length", arguments: JSON.stringify({ text: "Harness" }),
    }};
    const usage = finished
      ? { prompt_tokens: 150, completion_tokens: 9, total_tokens: 159,
          prompt_tokens_details: { cached_tokens: 60 }, completion_tokens_details: { reasoning_tokens: 4 } }
      : { prompt_tokens: 100, completion_tokens: 7, total_tokens: 107,
          prompt_tokens_details: { cached_tokens: 40 }, completion_tokens_details: { reasoning_tokens: 3 } };
    if (!body.stream) {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({
        id, object: "chat.completion", created: 1, model: body.model, usage,
        choices: [{ index: 0, finish_reason: finished ? "stop" : "tool_calls",
          message: finished ? { role: "assistant", content: "已验证字符统计为 7。" }
            : { role: "assistant", content: "", tool_calls: [toolCall] } }],
      }));
      return;
    }
    response.writeHead(200, { "Content-Type": "text/event-stream" });
    const send = (choices: unknown[], extra = {}) => response.write("data: " + JSON.stringify({
      id, object: "chat.completion.chunk", created: 1, model: body.model, choices, ...extra,
    }) + "\n\n");
    send([{ index: 0, delta: finished ? { role: "assistant", content: "已验证字符统计为 7。" }
      : { role: "assistant", tool_calls: [{ index: 0, ...toolCall }] }, finish_reason: null }]);
    send([{ index: 0, delta: {}, finish_reason: finished ? "stop" : "tool_calls" }]);
    send([], { usage });
    response.end("data: [DONE]\n\n");
  });
  await new Promise<void>((resolve) => stub.listen(0, "127.0.0.1", resolve));
  const address = stub.address() as { port: number };
  const headers = { Origin: "http://localhost:3100" };
  const values = Object.fromEntries(readFileSync(
    resolve(process.cwd(), "../../services/api/.env.admin"), "utf8",
  ).trim().split(/\r?\n/).map((line) => {
    const index = line.indexOf("="); return [line.slice(0, index), line.slice(index + 1)];
  }));
  let model = "";
  let conversation = "";
  const runs: string[] = [];
  const question = `Harness验收-${Date.now()}`;
  try {
    await page.goto("http://localhost:3100");
    await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
    await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
    await page.getByRole("button", { name: "进入工作台" }).click();
    await expect(page.getByRole("button", { name: "退出登录", exact: true })).toBeVisible();
    const created = await page.request.post("http://localhost:3100/api/v1/admin/models", {
      headers, data: { name: question, model: "harness-local-test",
        base_url: `http://127.0.0.1:${address.port}/v1`, api_key: "local-test-only",
        kind: "chat", provider: "custom", protocol: "openai", max_output_tokens: 64 },
    });
    expect(created.status()).toBe(201);
    model = (await created.json()).id;
    await page.reload();
    await page.getByLabel("选择模型").selectOption(model);
    await page.getByLabel("发送消息", { exact: true }).fill(question);
    await expect(page.locator(".token-estimate summary")).toContainText("预计输入约");
    await expect(page.locator(".token-estimate summary")).toContainText("单次输出上限 64 Token");
    for (const round of [1, 2]) {
      if (round === 2) await page.getByLabel("发送消息", { exact: true }).fill("再统计一次");
      const submitted = page.waitForResponse((response) =>
        /\/conversations\/[^/]+\/runs$/.test(response.url()) && response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "发送", exact: true }).click();
      const sent = await submitted;
      runs.push((await sent.json()).run_id);
      conversation = new URL(sent.url()).pathname.split("/").at(-2)!;
      await expect(page.getByLabel("会话累计 Token 用量")).toContainText(`输入 ${round * 250} · 输出 ${round * 16} Token`);
      await expect(page.getByLabel("本轮 Token 用量")).toHaveCount(round);
      await expect(page.getByLabel("本轮 Token 用量").last()).toContainText("输入 250 · 输出 16 Token · 2 次调用");
      expect(calls).toBe(round * 2);
      expect(requestedLimits).toEqual(Array(round * 2).fill(64));
      if (round === 1) {
        await page.reload();
        await page.getByRole("button", { name: question, exact: true }).click();
        await expect(page.getByLabel("本轮 Token 用量")).toContainText("输入 250 · 输出 16");
        await page.getByLabel("选择模型").selectOption(model);
      }
    }
    const process = page.locator(".chat-process").last();
    await process.locator(":scope > summary").click();
    const diagnostics = process.locator(".run-diagnostics");
    await diagnostics.locator(":scope > summary").click();
    await expect(diagnostics.getByLabel("运行诊断详情")).toContainText("调用上限 12 次");
    await expect(diagnostics.getByLabel("运行诊断详情")).toContainText("此运行未启用联网搜索");
    const firstCall = diagnostics.locator(".diagnostic-call").first();
    await firstCall.locator(":scope > summary").click();
    await expect(firstCall.getByText("其中缓存读取", { exact: true }).locator("..").locator("dd")).toHaveText("40");
    await expect(firstCall.getByText("其中推理 Token", { exact: true }).locator("..").locator("dd")).toHaveText("3");
    await expect(firstCall.getByText("其中缓存写入", { exact: true }).locator("..").locator("dd")).toHaveText("未报告");
    const observed = await (await page.request.get(
      `http://localhost:3100/api/v1/runs/${runs.at(-1)}/diagnostics`,
    )).json();
    expect(observed.budget.reported_tokens).toBe(266);
    expect(observed.calls[0].first_token_ms).toBeGreaterThanOrEqual(0);
    expect(observed.calls[0].duration_ms).toBeGreaterThanOrEqual(observed.calls[0].first_token_ms);
    await page.screenshot({ path: "test-results/harness-diagnostics-desktop.png", fullPage: true });
    await page.screenshot({ path: "test-results/harness-token-desktop.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: "test-results/harness-token-mobile.png", fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole("button", { name: "运营管理", exact: true }).click();
    await page.getByText("当前默认模式策略", { exact: true }).click();
    await expect(page.locator(".operations-page")).toContainText("只读检索、技能与已授权工具");
    const ledger = page.getByRole("region", { name: "模型调用记录", exact: true }).getByRole("row").filter({ hasText: question }).first();
    await ledger.getByText("Token 细分", { exact: true }).click();
    await expect(ledger).toContainText("缓存读取 60");
    await expect(ledger).toContainText("推理 4");
    await page.getByRole("button", { name: "运行记录", exact: true }).click();
    const record = page.locator(".run-history-card").filter({ hasText: question }).first().locator("..");
    await record.locator(":scope > .run-diagnostics > summary").click();
    await expect(record.getByLabel("运行诊断详情")).toContainText("已报告 266 Token");
  } finally {
    writeFileSync("test-results/harness-test-ids.json", JSON.stringify({ model, runs }));
    if (conversation) await page.request.delete(`http://localhost:3100/api/v1/conversations/${conversation}`, { headers });
    if (model) await page.request.delete(`http://localhost:3100/api/v1/admin/models/${model}`, { headers });
    await new Promise<void>((resolve) => stub.close(() => resolve()));
  }
});
