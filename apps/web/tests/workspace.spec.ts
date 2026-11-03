import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const origin = "http://localhost:3100";
const headers = { Origin: origin };

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

test("private projects, literal search, pin, move, dialogs and retained attachment", async ({
  page,
}) => {
  test.setTimeout(90000);
  const prefix = `会话空间-${Date.now()}`;
  let project = "";
  let conversation = "";
  try {
    await login(page);
    await page.getByRole("button", { name: "新建项目", exact: true }).click();
    await page.getByRole("dialog").getByLabel("项目名称").fill(prefix);
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "保存", exact: true })
      .click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    const projects = await (
      await page.request.get(`${origin}/api/v1/conversation-projects`)
    ).json();
    project = projects.items.find(
      (item: { name: string }) => item.name === prefix,
    ).id;
    const created = await page.request.post(`${origin}/api/v1/conversations`, {
      headers,
      data: { title: prefix, project_id: project },
    });
    expect(created.status()).toBe(201);
    conversation = (await created.json()).id;
    const uploaded = await page.request.post(
      `${origin}/api/v1/conversations/${conversation}/attachments?name=workspace.txt`,
      { headers, data: Buffer.from("项目删除后应保留的附件") },
    );
    expect(uploaded.status()).toBe(201);
    const attachment = (await uploaded.json()).id;
    await page.reload();
    await page
      .getByTitle(prefix, { exact: true })
      .filter({ has: page.locator("svg") })
      .click();
    await page.getByRole("button", { name: prefix, exact: true }).click();
    await expect(page.locator(".pending-attachments")).toContainText(
      "workspace.txt",
    );
    await page.getByLabel("搜索对话内容").fill(prefix);
    await page.getByLabel("搜索对话内容").press("Enter");
    await expect(page.locator(".conversation-list .conversation")).toHaveCount(
      1,
    );
    await page
      .getByRole("button", { name: `对话操作 ${prefix}`, exact: true })
      .click();
    await page
      .getByRole("button", { name: `重命名 ${prefix}`, exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByLabel("对话名称")
      .fill(prefix + "-已整理");
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "保存", exact: true })
      .click();
    const title = prefix + "-已整理";
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await page
      .getByRole("button", { name: `对话操作 ${title}`, exact: true })
      .click();
    await page.getByRole("button", { name: "置顶对话", exact: true }).click();
    await expect(page.getByLabel("已置顶")).toHaveCount(1);
    await page
      .getByRole("button", { name: `对话操作 ${title}`, exact: true })
      .click();
    await page.getByRole("button", { name: "移动到项目", exact: true }).click();
    await page.getByRole("dialog").getByLabel("所属项目").selectOption("");
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "保存", exact: true })
      .click();
    await expect(page.locator(".conversation-list .conversation")).toHaveCount(
      0,
    );
    await page.getByRole("button", { name: "未分组", exact: true }).click();
    await expect(page.locator(".conversation-list .conversation")).toHaveCount(
      1,
    );
    await page
      .getByRole("button", { name: `对话操作 ${title}`, exact: true })
      .click();
    await page.getByRole("button", { name: "移动到项目", exact: true }).click();
    await page.getByRole("dialog").getByLabel("所属项目").selectOption(project);
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "保存", exact: true })
      .click();
    await page.getByTitle(prefix, { exact: true }).click();
    await expect(page.locator(".conversation-list .conversation")).toHaveCount(
      1,
    );
    await page.getByRole("button", { name: "重命名项目", exact: true }).click();
    await page
      .getByRole("dialog")
      .getByLabel("项目名称")
      .fill(prefix + "-项目");
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "保存", exact: true })
      .click();
    await expect(
      page.getByTitle(prefix + "-项目", { exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/workspace-desktop.png",
      fullPage: true,
    });
    await page.getByRole("button", { name: "删除项目", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("消息和附件保留");
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "确认删除", exact: true })
      .click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    project = "";
    const retained = await (
      await page.request.get(`${origin}/api/v1/conversations/${conversation}`)
    ).json();
    expect(retained.project_id).toBeNull();
    expect(retained.pinned).toBe(true);
    expect(
      await (
        await page.request.get(
          `${origin}/api/v1/attachments/${attachment}/download`,
        )
      ).text(),
    ).toBe("项目删除后应保留的附件");
    await page.reload();
    await page.getByTitle(title, { exact: true }).click();
    await expect(page.locator(".pending-attachments")).toContainText(
      "workspace.txt",
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "展开侧边栏", exact: true }).click();
    await page.getByRole("button", { name: "新建项目", exact: true }).click();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: "test-results/workspace-mobile-dialog.png",
      fullPage: true,
    });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await page.setViewportSize({ width: 1440, height: 900 });
    await page
      .getByRole("button", { name: `对话操作 ${title}`, exact: true })
      .click();
    await page.getByRole("button", { name: "删除对话", exact: true }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "取消", exact: true })
      .click();
    expect(
      (
        await page.request.get(`${origin}/api/v1/conversations/${conversation}`)
      ).status(),
    ).toBe(200);
    await page.route(
      "**/api/v1/conversations?**",
      (route) =>
        route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            error: { code: "TEST_UNAVAILABLE", message: "会话列表暂不可用" },
          }),
        }),
      { times: 1 },
    );
    await page
      .getByRole("button", { name: `对话操作 ${title}`, exact: true })
      .click();
    await page
      .getByRole("button", { name: `重命名 ${title}`, exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "保存", exact: true })
      .click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await expect(page.locator(".sidebar-error")).toContainText(
      "会话列表暂不可用",
    );
    await page.getByRole("button", { name: "重试", exact: true }).click();
    await expect(page.locator(".sidebar-error")).not.toBeVisible();
    await page
      .getByRole("button", { name: `对话操作 ${title}`, exact: true })
      .click();
    await page.getByRole("button", { name: "删除对话", exact: true }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "确认删除", exact: true })
      .click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    expect(
      (
        await page.request.get(`${origin}/api/v1/conversations/${conversation}`)
      ).status(),
    ).toBe(404);
    conversation = "";
  } finally {
    if (project)
      await page.request.delete(
        `${origin}/api/v1/conversation-projects/${project}`,
        { headers },
      );
    if (conversation)
      await page.request.delete(
        `${origin}/api/v1/conversations/${conversation}`,
        { headers },
      );
  }
});

test("knowledge evidence diagnostics and saved keyword threshold on the actual page", async ({
  page,
}) => {
  test.setTimeout(60000);
  await login(page);
  const name = `证据页面-${Date.now()}`;
  const created = await page.request.post(
    `${origin}/api/v1/capabilities/knowledge`,
    { headers, data: { name } },
  );
  expect(created.status()).toBe(201);
  const id = (await created.json()).id;
  try {
    expect(
      (
        await page.request.post(
          `${origin}/api/v1/capabilities/knowledge/${id}/documents`,
          {
            headers,
            multipart: {
              file: {
                name: "证据.txt",
                mimeType: "text/plain",
                buffer: Buffer.from("平台服务价格每月29元；API Key 不回显。"),
              },
            },
          },
        )
      ).status(),
    ).toBe(201);
    await page.getByRole("button", { name: "能力中心", exact: true }).click();
    await page
      .getByRole("article")
      .filter({ hasText: name })
      .getByRole("button", { name: "管理文档与检索", exact: true })
      .click();
    await page.getByRole("tab", { name: "配置", exact: true }).click();
    await page.getByLabel("最低关键词覆盖率").fill("25");
    await page
      .getByRole("button", { name: "保存检索策略", exact: true })
      .click();
    await page.getByRole("tab", { name: "检索测试", exact: true }).click();
    await page.getByLabel("检索问题").fill("平台创始人的身高是多少？");
    await page.getByRole("button", { name: "检索测试", exact: true }).click();
    await expect(page.locator(".knowledge-results")).toContainText(
      "候选证据 0 个片段",
    );
    await expect(page.locator(".knowledge-results")).toContainText(
      "筛除 1 个弱关键词匹配",
    );
    await expect(page.locator(".knowledge-results")).toContainText(
      "没有通过筛选",
    );
    await page.getByLabel("检索问题").fill("API Key");
    await page.getByRole("button", { name: "检索测试", exact: true }).click();
    await expect(page.locator(".knowledge-results")).toContainText(
      "候选证据 1 个片段",
    );
    await expect(page.locator(".knowledge-results")).toContainText(
      "分数不是回答置信度",
    );
    await page.screenshot({
      path: "test-results/retrieval-evidence-desktop.png",
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "返回知识库", exact: true })
      .click();
    await page
      .getByRole("article")
      .filter({ hasText: name })
      .getByRole("button", { name: "管理文档与检索", exact: true })
      .click();
    await page.getByRole("tab", { name: "配置", exact: true }).click();
    await expect(page.getByLabel("最低关键词覆盖率")).toHaveValue("25");
  } finally {
    await page.request.delete(`${origin}/api/v1/capabilities/knowledge/${id}`, {
      headers,
    });
  }
});
