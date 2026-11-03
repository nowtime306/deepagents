import { expect, test, type Page } from "@playwright/test";

test.use({ baseURL: "http://127.0.0.1:3101", trace: "off", screenshot: "off", video: "off" });

async function fixture(page: Page, login = true) {
  const state = { logged: login, pending: "", mfa: false, failedSave: false,
    calls: [] as string[], payloads: [] as Record<string, unknown>[],
    providers: [{ id: "qa-idp", name: "本机企业", issuer: "https://idp.invalid", client_id: "qa-client",
      enabled: true, allow_registration: false, allowed_domains: [] as string[] }],
    servers: [{ id: "qa-mcp", name: "本机工具", url: "https://mcp.invalid", enabled: true,
      transport: "sse", tools: [], status: "available" }], grant: true };
  await page.route("**/api/v1/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace("/api/v1", "");
    const method = request.method(); state.calls.push(`${method} ${path}`);
    let body: Record<string, unknown> = {};
    if (request.postData()) { try { body = request.postDataJSON(); } catch { /* no binary inputs here */ } }
    const fail = (status: number, message: string) => route.fulfill({ status, json: { error: { message } } });
    let data: unknown = { items: [] };
    if (path === "/auth/me") {
      if (!state.logged) return fail(401, "请先登录");
      data = { id: "qa-person", username: "隔离界面验收", is_admin: true };
    }
    if (path === "/auth/registration") data = { registration_mode: "approval" };
    if (path === "/auth/identity/providers") data = { items: state.providers };
    if (path === "/auth/security/pending") data = { challenge: state.pending, mfa_required: !!state.pending };
    if (path === "/auth/login") {
      state.pending = "qa-local-challenge";
      return route.fulfill({ status: 202, json: { mfa_required: true, challenge: state.pending } });
    }
    if (path === "/auth/security/challenge") return fail(401, "验证已失效，请重新登录");
    if (path === "/auth/security/pending/cancel") { state.pending = ""; data = { cancelled: true }; }
    if (path === "/auth/identity/recovery/request") data = { message: "如账号可找回，将发送凭据" };
    if (path === "/auth/identity/recovery/reset") { state.payloads.push(body); data = { updated: true }; }
    if (path === "/admin/identity/providers") data = { items: state.providers };
    if (path.startsWith("/admin/identity/providers/") && method === "PUT") {
      if (state.failedSave) return fail(503, "认证来源保存失败，请重试");
      state.payloads.push(body); state.providers[0] = { ...state.providers[0], ...body };
      data = { saved: true };
    }
    if (path === "/auth/security") data = { enabled: state.mfa, recovery_remaining: state.mfa ? 2 : 0 };
    if (path === "/auth/security/mfa/enroll") data = { secret: "UI-MOCK-NOT-A-REAL-SEED" };
    if (path === "/auth/security/mfa/confirm") { state.mfa = true; data = { recovery_codes: ["UI-MOCK-ONE", "UI-MOCK-TWO"] }; }
    if (path === "/auth/security/contacts") data = { items: [] };
    if (path === "/auth/identity/bindings") data = { items: [] };
    if (path === "/models") data = { items: [] };
    if (path === "/admin/models/catalog") data = { kinds: [], providers: [], protocols: [] };
    if (path === "/capabilities/bindings") data = { knowledge_ids: [], skill_ids: [], mcp_ids: [] };
    if (path === "/capabilities/mcp") {
      if (method === "POST") { state.payloads.push(body); data = { id: "created" }; }
      else data = { items: state.servers };
    }
    if (path.endsWith("/status") && path.startsWith("/tool-oauth/")) data = {
      configured: true, grant: state.grant ? { expires_at: "2030-01-01T00:00:00Z" } : null };
    if (path.endsWith("/client") && path.startsWith("/tool-oauth/")) {
      if (method === "PUT") state.payloads.push(body);
      data = { client_id: "qa-client", scopes: "read" };
    }
    if (path.endsWith("/grant") && method === "DELETE") { state.grant = false; data = { revoked: true }; }
    await route.fulfill({ json: data });
  });
  return state;
}

test("PE-TC-041 local MFA202 never enters app and expired challenge can restart", async ({ page }) => {
  const state = await fixture(page, false); await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("qa-person");
  await page.getByLabel("密码", { exact: true }).fill("ui-only-placeholder");
  await page.getByRole("button", { name: "进入工作台", exact: true }).click();
  await expect(page.getByLabel("动态验证码或一次恢复码")).toBeVisible();
  await expect(page.getByRole("button", { name: "个人设置", exact: true })).toHaveCount(0);
  await page.getByLabel("动态验证码或一次恢复码").fill("000000");
  await page.getByRole("button", { name: "完成身份验证", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "验证已失效" })).toBeVisible();
  await page.getByRole("button", { name: "返回登录，重新开始" }).click();
  await expect(page.getByRole("button", { name: "进入工作台", exact: true })).toBeVisible();
  expect(state.calls).toContain("POST /auth/security/pending/cancel");
});

test("PE-TC-035 enterprise pending challenge returns to enterprise and local login", async ({ page }) => {
  const state = await fixture(page, false); state.pending = "qa-enterprise-challenge";
  await page.goto("/"); await expect(page.getByLabel("动态验证码或一次恢复码")).toBeVisible();
  await page.getByRole("button", { name: "返回登录，重新开始" }).click();
  await expect(page.getByRole("button", { name: "本机企业登录", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "进入工作台", exact: true })).toBeVisible();
});

test("PE-TC-051/053 recovery UI keeps MFA proof and uses generic request response", async ({ page }) => {
  const state = await fixture(page, false); await page.goto("/");
  await page.getByRole("button", { name: "找回密码", exact: true }).click();
  const region = page.getByRole("region", { name: "企业登录与身份验证" });
  await region.getByLabel("账号", { exact: true }).fill("unknown-qa");
  await region.getByLabel("找回方式").selectOption("sms");
  await region.getByRole("button", { name: "发送找回凭据" }).click();
  await expect(region.getByRole("status")).toHaveText("如账号可找回，将发送凭据");
  await region.getByLabel("收到的找回凭据").fill("UI-ONLY-PLACEHOLDER");
  await region.getByLabel("新密码", { exact: true }).fill("UI-only-replacement");
  await region.getByLabel("动态验证码或恢复码（已启用验证器时必填）").fill("000000");
  await region.getByRole("button", { name: "更新密码并撤销旧会话" }).click();
  await expect(region.getByRole("status")).toHaveText("密码已更新，请重新登录");
  expect(state.payloads.at(-1)?.mfa_code === "000000").toBeTruthy();
});

test("PE-TC-030 identity provider save failure preserves fields and issuer stays immutable", async ({ page }) => {
  const state = await fixture(page); await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "编辑本机企业", exact: true }).click();
  await expect(page.getByLabel("Issuer地址")).toBeDisabled();
  await page.getByLabel("显示名称", { exact: true }).fill("修改后的企业");
  state.failedSave = true;
  await page.getByRole("button", { name: "保存认证来源", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "认证来源保存失败" })).toBeVisible();
  await expect(page.getByLabel("显示名称", { exact: true })).toHaveValue("修改后的企业");
  state.failedSave = false;
  await page.getByRole("button", { name: "保存认证来源", exact: true }).click();
  await expect(page.getByRole("button", { name: "编辑修改后的企业", exact: true })).toBeVisible();
  expect(state.payloads.at(-1)?.secret === "").toBeTruthy();
});

test("PE-TC-040/042 personal security displays recovery codes once", async ({ page }) => {
  await fixture(page); await page.goto("/");
  await page.getByRole("button", { name: "个人设置", exact: true }).click();
  const region = page.getByRole("region", { name: "个人身份安全" });
  await region.getByRole("button", { name: "配置验证器", exact: true }).click();
  await region.getByLabel("动态验证码或恢复码", { exact: true }).fill("000000");
  await region.getByRole("button", { name: "确认启用多因素验证", exact: true }).click();
  await expect(region.getByText("恢复码仅显示这一次，请离线保存，每个只能使用一次。")).toBeVisible();
  await region.getByRole("button", { name: "我已保存", exact: true }).click();
  await expect(region.getByText("UI-MOCK-ONE", { exact: true })).toHaveCount(0);
  await page.reload(); await page.getByRole("button", { name: "个人设置", exact: true }).click();
  await expect(region.getByText("已启用 · 剩余 2 个恢复码")).toBeVisible();
});

for (const transport of ["sse", "stdio", "acp"]) {
  test(`PE-TC-060 transport ${transport} form sends correct protocol and scope`, async ({ page }) => {
    const state = await fixture(page); await page.goto("/");
    await page.getByRole("button", { name: "能力中心", exact: true }).click();
    await page.getByRole("button", { name: "MCP 工具", exact: true }).click();
    await page.getByRole("button", { name: "添加 MCP", exact: true }).click();
    await page.getByLabel("服务名称", { exact: true }).fill("协议验收");
    await page.getByRole("combobox", { name: "传输协议", exact: true }).selectOption(transport);
    if (transport === "sse") await page.getByLabel("Streamable HTTP 地址").fill("https://mcp.invalid/sse");
    else {
      await expect(page.getByLabel("Streamable HTTP 地址")).toHaveCount(0);
      await page.getByLabel("隔离镜像命令参数（JSON数组）").fill('["/usr/local/bin/python","/workspace/qa.py"]');
      await expect(page.getByText("不在宿主执行，不自动下载安装包。", { exact: false })).toBeVisible();
    }
    await page.getByRole("button", { name: "保存 MCP", exact: true }).click();
    await expect.poll(() => state.payloads.length).toBe(1);
    expect(state.payloads[0].transport).toBe(transport);
    if (transport !== "sse") expect(state.payloads[0].url).toBe("");
  });
}

test("PE-TC-070/072 OAuth component loads per-account state and revokes", async ({ page }) => {
  const state = await fixture(page); await page.goto("/");
  await page.getByRole("button", { name: "能力中心", exact: true }).click();
  await page.getByRole("button", { name: "MCP 工具", exact: true }).click();
  await page.getByText("OAuth授权（本人账号）", { exact: true }).click();
  await expect(page.getByRole("button", { name: "撤销本人授权", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "撤销本人授权", exact: true }).click();
  await expect(page.getByText("尚未授权或授权已撤销", { exact: true })).toBeVisible();
  expect(state.calls).toContain("DELETE /tool-oauth/qa-mcp/grant");
});

test("PE-TC-150 mobile login identity controls stay within viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page, false); await page.goto("/");
  await page.getByRole("button", { name: "找回密码", exact: true }).click();
  await expect(page.getByRole("button", { name: "本机企业登录", exact: true })).toBeVisible();
  await expect(page.getByLabel("新密码", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});

test("PE-TC-030 provider name and enabled state combine without mutating sources", async ({ page }) => {
  const state = await fixture(page);
  state.providers.push({ ...state.providers[0], id: "qa-disabled", name: "停用团队", enabled: false });
  await page.goto("/"); await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("searchbox", { name: "搜索认证来源" }).fill("团队");
  await expect(page.getByRole("button", { name: "编辑停用团队", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "编辑本机企业", exact: true })).toHaveCount(0);
  await page.getByRole("combobox", { name: "认证来源状态" }).selectOption("enabled");
  await expect(page.getByRole("button", { name: "编辑停用团队", exact: true })).toHaveCount(0);
  await page.getByRole("combobox", { name: "认证来源状态" }).selectOption("disabled");
  await expect(page.getByRole("button", { name: "编辑停用团队", exact: true })).toBeVisible();
  expect(state.calls.some(call => call.startsWith("PUT /admin/identity/providers/"))).toBeFalsy();
});

test("PE-TC-150 provider empty search can clear on mobile and retains list actions", async ({ page }) => {
  await fixture(page); await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  const search = page.getByRole("searchbox", { name: "搜索认证来源" });
  await search.fill("不存在的来源");
  await expect(page.getByRole("button", { name: "编辑本机企业", exact: true })).toHaveCount(0);
  await search.fill("");
  await expect(page.getByRole("button", { name: "编辑本机企业", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "新增认证来源", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
});
