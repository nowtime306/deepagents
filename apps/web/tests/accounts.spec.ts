import { expect, test, type Page } from "@playwright/test";
import { readFileSync, existsSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const origin = "http://localhost:3100";
const headers = { Origin: origin };
const password = "Account-validation-2026";
test.beforeEach(async ({ page }) => {
  await expect.poll(async () => {
    try { return (await page.request.get("http://127.0.0.1:8100/health/ready")).status(); }
    catch { return 0; }
  }, { timeout: 15000 }).toBe(200);
});
function remember(kind: string, value: Record<string, string>) {
  const path = resolve("test-results/account-test-ids.json");
  const data = existsSync(path) ? JSON.parse(readFileSync(path, "utf8")) : [];
  data.push({ kind, ...value });
  writeFileSync(path, JSON.stringify(data));
}
function admin() {
  return Object.fromEntries(
    readFileSync(resolve("../../services/api/.env.admin"), "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const i = line.indexOf("=");
        return [line.slice(0, i), line.slice(i + 1)];
      }),
  );
}
async function login(page: Page, username: string, secret: string) {
  await page.goto(origin);
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(secret);
  await page.getByRole("button", { name: "进入工作台", exact: true }).click();
}
async function adminLogin(page: Page) {
  const values = admin();
  await login(page, values.ADMIN_USERNAME, values.ADMIN_PASSWORD);
  await expect(
    page.getByRole("button", { name: "账号管理", exact: true }),
  ).toBeVisible();
}

test("actual registration approval, viewer permissions and password revocation", async ({
  page,
  browser,
}) => {
  test.setTimeout(90000);
  const username = `acct-test-${Date.now()}`;
  let id = "";
  const userContext = await browser.newContext();
  const userPage = await userContext.newPage();
  try {
    await userPage.goto(origin);
    await userPage
      .getByRole("button", { name: "注册账号", exact: true })
      .click();
    await userPage.getByLabel("账号", { exact: true }).fill(username);
    await userPage.getByLabel("密码", { exact: true }).fill(password);
    await userPage.getByLabel("确认密码", { exact: true }).fill(password);
    await userPage
      .getByRole("button", { name: "提交注册", exact: true })
      .click();
    await expect(userPage.getByRole("status")).toContainText("等待管理员审核");
    await adminLogin(page);
    await page.getByRole("button", { name: "账号管理", exact: true }).click();
    await page.getByLabel("搜索账号", { exact: true }).fill(username);
    await page
      .getByRole("button", { name: "执行账号搜索", exact: true })
      .click();
    const row = page.getByRole("row").filter({ hasText: username });
    await expect(row).toContainText("待审核");
    id = (
      await (
        await page.request.get(`${origin}/api/v1/admin/accounts?q=${username}`)
      ).json()
    ).items[0].id;
    remember("user", { id, username });
    await row.getByRole("button", { name: "权限与状态", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("分配角色").selectOption("viewer");
    await dialog.getByLabel("审核状态").selectOption("approved");
    await dialog.getByRole("button", { name: "保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await login(userPage, username, password);
    await expect(
      userPage.getByRole("button", { name: "新建对话", exact: true }),
    ).toBeDisabled();
    expect(
      (
        await userPage.request.post(`${origin}/api/v1/conversations`, {
          headers,
          data: { title: "不允许写入" },
        })
      ).status(),
    ).toBe(403);
    expect(
      (await userPage.request.get(`${origin}/api/v1/admin/accounts`)).status(),
    ).toBe(403);
    const oldCookie = (await userContext.cookies()).find(
      (cookie) => cookie.name === "owner_agent_session",
    )!.value;
    await userPage
      .getByRole("button", { name: "个人设置", exact: true })
      .click();
    await userPage.getByLabel("原密码", { exact: true }).fill(password);
    await userPage
      .getByLabel("新密码", { exact: true })
      .fill(password + "-new");
    await userPage
      .getByLabel("确认新密码", { exact: true })
      .fill(password + "-wrong");
    await userPage
      .getByRole("button", { name: "修改密码并退出旧会话", exact: true })
      .click();
    await expect(userPage.getByRole("alert").filter({ hasText: "两次输入的新密码不一致" })).toBeVisible();
    await userPage
      .getByLabel("确认新密码", { exact: true })
      .fill(password + "-new");
    await userPage
      .getByRole("button", { name: "修改密码并退出旧会话", exact: true })
      .click();
    await expect(
      userPage.getByRole("button", { name: "进入工作台", exact: true }),
    ).toBeVisible();
    expect(
      (
        await page.request.get(`${origin}/api/v1/auth/me`, {
          headers: { Cookie: `owner_agent_session=${oldCookie}` },
        })
      ).status(),
    ).toBe(401);
    await login(userPage, username, password + "-new");
    await expect(
      userPage.getByRole("button", { name: "退出登录", exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/accounts-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: "test-results/accounts-mobile.png",
      fullPage: true,
    });
  } finally {
    if (id)
      await page.request.put(`${origin}/api/v1/admin/accounts/${id}`, {
        headers,
        data: {
          role_id: "viewer",
          enabled: false,
          approval_status: "approved",
        },
      });
    await userContext.close();
  }
});

test("actual administrator create and reset require the user to change temporary password", async ({
  page,
  browser,
}) => {
  test.setTimeout(90000);
  await adminLogin(page);
  await page.getByRole("button", { name: "账号管理", exact: true }).click();
  const username = `acct-test-${Date.now()}`;
  let id = "";
  const context = await browser.newContext();
  const user = await context.newPage();
  try {
    await page.getByRole("button", { name: "创建账号", exact: true }).click();
    await page.getByRole("dialog").getByLabel("账号名称").fill(username);
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "保存", exact: true })
      .click();
    const temporary = await page
      .getByRole("dialog")
      .getByLabel("临时密码")
      .inputValue();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "我已保存", exact: true })
      .click();
    id = (
      await (
        await page.request.get(`${origin}/api/v1/admin/accounts?q=${username}`)
      ).json()
    ).items[0].id;
    remember("user", { id, username });
    await login(user, username, temporary);
    await expect(
      user.getByRole("heading", { name: "请先修改临时密码", exact: true }),
    ).toBeVisible();
    expect(
      (await user.request.get(`${origin}/api/v1/conversations`)).status(),
    ).toBe(403);
    await user.getByLabel("原密码", { exact: true }).fill(temporary);
    await user.getByLabel("新密码", { exact: true }).fill(password);
    await user.getByLabel("确认新密码", { exact: true }).fill(password);
    await user
      .getByRole("button", { name: "修改密码并退出旧会话", exact: true })
      .click();
    await expect(user.getByRole("button", { name: "进入工作台", exact: true })).toBeVisible();
    await login(user, username, password);
    await expect(
      user.getByRole("button", { name: "退出登录", exact: true }),
    ).toBeVisible();
    await page.getByLabel("搜索账号", { exact: true }).fill(username);
    await page
      .getByRole("button", { name: "执行账号搜索", exact: true })
      .click();
    await page
      .getByRole("row")
      .filter({ hasText: username })
      .getByRole("button", { name: "重置密码", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "确认重置", exact: true })
      .click();
    const reset = await page
      .getByRole("dialog")
      .getByLabel("临时密码")
      .inputValue();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "我已保存", exact: true })
      .click();
    await user.reload();
    await expect(
      user.getByRole("button", { name: "进入工作台", exact: true }),
    ).toBeVisible();
    await login(user, username, reset);
    await expect(
      user.getByRole("heading", { name: "请先修改临时密码", exact: true }),
    ).toBeVisible();
  } finally {
    if (id)
      await page.request.put(`${origin}/api/v1/admin/accounts/${id}`, {
        headers,
        data: {
          role_id: "member",
          enabled: false,
          approval_status: "approved",
        },
      });
    await context.close();
  }
});

test("actual custom role and invitation grant only selected capabilities", async ({
  page,
  browser,
}) => {
  test.setTimeout(90000);
  await adminLogin(page);
  await page.getByRole("button", { name: "账号管理", exact: true }).click();
  const name = `角色验收-${Date.now()}`;
  const username = `acct-test-${Date.now()}`;
  let role = "",
    id = "",
    invitationId = "";
  const context = await browser.newContext();
  const user = await context.newPage();
  const settings = await (
    await page.request.get(`${origin}/api/v1/admin/account-settings`)
  ).json();
  try {
    await page.getByRole("button", { name: "功能角色", exact: true }).click();
    await page.getByRole("button", { name: "新增角色", exact: true }).click();
    await page.getByRole("dialog").getByLabel("角色名称").fill(name);
    await page
      .getByRole("dialog")
      .getByRole("checkbox", { name: "查看对话", exact: true })
      .check();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "保存", exact: true })
      .click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    role = (
      await (await page.request.get(`${origin}/api/v1/admin/roles`)).json()
    ).items.find((item: { name: string }) => item.name === name).id;
    remember("role", { id: role });
    await page.getByRole("button", { name: "邀请与注册", exact: true }).click();
    await page.getByLabel("注册方式", { exact: true }).selectOption("closed");
    await page
      .getByRole("button", { name: "保存注册方式", exact: true })
      .click();
    await expect(page.getByRole("status")).toContainText("注册方式已更新");
    await page.getByRole("button", { name: "创建邀请", exact: true }).click();
    await page.getByRole("dialog").getByLabel("邀请名称").fill(name);
    await page.getByRole("dialog").getByLabel("分配角色").selectOption(role);
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "保存", exact: true })
      .click();
    const invitation = await page
      .getByRole("dialog")
      .getByLabel("邀请码", { exact: true })
      .inputValue();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "我已保存", exact: true })
      .click();
    invitationId = (
      await (
        await page.request.get(`${origin}/api/v1/admin/invitations`)
      ).json()
    ).items.find((item: { name: string }) => item.name === name).id;
    remember("invitation", { id: invitationId });
    await user.goto(origin);
    await user.getByRole("button", { name: "注册账号", exact: true }).click();
    await user.getByLabel("账号", { exact: true }).fill(username);
    await user.getByLabel("密码", { exact: true }).fill(password);
    await user.getByLabel("确认密码", { exact: true }).fill(password);
    await user.getByLabel("邀请码", { exact: true }).fill(invitation);
    await user.getByRole("button", { name: "提交注册", exact: true }).click();
    await expect(user.getByRole("status")).toContainText("可以登录");
    id = (
      await (
        await page.request.get(`${origin}/api/v1/admin/accounts?q=${username}`)
      ).json()
    ).items[0].id;
    remember("user", { id, username });
    await login(user, username, password);
    await expect(
      user.getByRole("button", { name: "新建对话", exact: true }),
    ).toBeDisabled();
    await expect(
      user.getByRole("button", { name: "任务工作台", exact: true }),
    ).toHaveCount(0);
    expect((await user.request.get(`${origin}/api/v1/agents`)).status()).toBe(
      403,
    );
    await page.getByRole("button", { name: "功能角色", exact: true }).click();
    await page
      .getByRole("article")
      .filter({ hasText: name })
      .getByRole("button", { name: "编辑角色", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByRole("checkbox", { name: "管理对话与附件", exact: true })
      .check();
    await page
      .getByRole("dialog")
      .getByRole("checkbox", { name: "发起对话与联网", exact: true })
      .check();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "保存", exact: true })
      .click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await user.reload();
    await expect(
      user.getByRole("button", { name: "进入工作台", exact: true }),
    ).toBeVisible();
    await login(user, username, password);
    await expect(
      user.getByRole("button", { name: "新建对话", exact: true }),
    ).toBeEnabled();
  } finally {
    await page.request.put(`${origin}/api/v1/admin/account-settings`, {
      headers,
      data: { registration_mode: settings.registration_mode },
    });
    if (id)
      await page.request.put(`${origin}/api/v1/admin/accounts/${id}`, {
        headers,
        data: {
          role_id: "member",
          enabled: false,
          approval_status: "approved",
        },
      });
    if (invitationId)
      await page.request.delete(
        `${origin}/api/v1/admin/invitations/${invitationId}`,
        { headers },
      );
    if (role)
      await page.request.delete(`${origin}/api/v1/admin/roles/${role}`, {
        headers,
      });
    await context.close();
  }
});
