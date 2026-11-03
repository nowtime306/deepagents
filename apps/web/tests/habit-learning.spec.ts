import { expect, test, type Page } from "@playwright/test";

type Preference = { id: string; kind: string; value: string | null; status: string; evidence_count: number; revision: number; skill_id: string | null; skill_version: number | null; skill_enabled: boolean | null; manual_confirmed: boolean; updated_at: string };
const candidate = (): Preference => ({ id: "preference", kind: "language", value: "zh-CN", status: "learning", evidence_count: 2, revision: 4, skill_id: null, skill_version: null, skill_enabled: null, manual_confirmed: false, updated_at: new Date().toISOString() });

async function setup(page: Page, seed: Preference | null = null, readOnly = false, stale = false) {
  let profile = seed, enabled = false, installed = !!seed;
  let conflict = stale;
  const requests: { path: string; method: string; body: unknown }[] = [];
  const bindings = { knowledge_ids: ["knowledge-existing"], skill_ids: installed ? ["manual", "meta"] : ["manual"], mcp_ids: ["mcp-existing"] };
  const config = () => ({ enabled, meta_skill_id: installed ? "meta" : null, meta_skill_bound: bindings.skill_ids.includes("meta"), meta_skill_enabled: true, threshold: 3, scope: "global", preferences: profile ? [profile] : [], options: { language: [{ value: "zh-CN", label: "中文回复" }, { value: "en", label: "英文回复" }] } });
  const skills = () => [{ id: "manual", name: "existing-manual", description: "原有手工技能", version: 3, enabled: true }, ...(installed ? [{ id: "meta", name: "habit-skill-builder", description: "习惯元技能", version: 1, enabled: true }] : []), ...(profile?.skill_id ? [{ id: "child", name: "learned-language", description: "学习生成的回复偏好", version: profile.skill_version!, enabled: profile.skill_enabled! }] : [])];
  await page.route("**/api/v1/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace("/api/v1", "");
    const method = request.method(), body = request.postData() ? request.postDataJSON() : null;
    requests.push({ path, method, body });
    let data: unknown = { items: [] }, status = 200;
    if (path === "/auth/me") data = { username: "习惯组件测试", is_admin: !readOnly, ...(readOnly ? { permissions: ["skills.read", "chat.read"] } : {}) };
    if (path === "/models") data = { items: [{ id: "unused", name: "不请求的模型" }] };
    if (path === "/capabilities/bindings") data = bindings;
    if (path === "/capabilities/skills") data = { items: skills() };
    if (path === "/capabilities/habits/install") { installed = true; if (!bindings.skill_ids.includes("meta")) bindings.skill_ids.push("meta"); data = { skill_id: "meta", version: 1, bound: true }; }
    if (path === "/capabilities/habits") { if (method === "PUT") enabled = body.enabled; data = config(); }
    if (path.endsWith("/preferences/preference/correct")) {
      if (conflict) { conflict = false; profile = { ...profile!, revision: 8 }; status = 409; data = { error: { message: "习惯记录已更新，请刷新后重试" } }; }
      else { profile = { ...profile!, value: body.value, status: "active", revision: profile!.revision + 1, skill_id: "child", skill_version: 1, skill_enabled: true, manual_confirmed: true }; data = profile; }
    }
    if (path === "/capabilities/habits/preferences/preference") {
      if (method === "DELETE") profile = { ...profile!, value: null, status: "forgotten", revision: profile!.revision + 1, evidence_count: 0, skill_id: null, skill_version: null, skill_enabled: null, manual_confirmed: false };
      if (method === "PATCH") profile = { ...profile!, status: body.enabled ? (profile!.skill_id ? "active" : "learning") : "disabled", skill_enabled: profile!.skill_id ? body.enabled : null, revision: profile!.revision + 1 };
      data = profile;
    }
    if (path.endsWith("/preferences/preference/relearn")) { profile = { ...profile!, status: "learning", evidence_count: 0, skill_version: null, manual_confirmed: false, revision: profile!.revision + 1 }; data = profile; }
    if (path.endsWith("/preferences/preference/evidence")) data = { items: [{ message_id: "message-source", conversation_id: "source", created_at: new Date().toISOString(), available: true }] };
    if (path === "/capabilities/skills/child" && method === "PATCH") { profile = { ...profile!, skill_enabled: false }; data = { ok: true }; }
    else if (path === "/capabilities/skills/child" || path === "/capabilities/skills/meta") data = { name: "习惯技能", files: { "SKILL.md": "---\nname: learned-language\ndescription: 回复偏好\n---\n使用英文回复，当前明确要求优先。" } };
    if (path === "/capabilities/skills/manual") data = { name: "existing-manual", files: { "SKILL.md": "原有手工技能内容" } };
    if (path === "/conversations") data = { items: [{ id: "source", title: "习惯来源", archived: false }] };
    if (path === "/conversations/source") data = { id: "source", title: "习惯来源", archived: false };
    if (path === "/conversations/source/messages") data = { items: [{ id: "message-source", role: "user", content: { text: "以后默认用中文回复，这是来源消息。" } }], pending_attachments: [] };
    await route.fulfill({ status, json: data });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "能力中心", exact: true }).click();
  if (!readOnly) await page.getByRole("button", { name: "Skills 技能", exact: true }).click();
  const panel = page.getByRole("region", { name: "习惯学习管理", exact: true });
  await expect(panel.getByLabel("开启习惯学习")).not.toBeChecked();
  return { panel, requests, bindings };
}

test("enabling installs then enables without replacing existing capability selections", async ({ page }) => {
  const { panel, requests, bindings } = await setup(page);
  await panel.getByLabel("开启习惯学习").click();
  await expect(panel.getByLabel("开启习惯学习")).toBeChecked();
  await expect(panel.getByRole("button", { name: "刷新习惯", exact: true })).toBeEnabled();
  const mutations = requests.filter(item => item.method !== "GET");
  expect(mutations.map(item => item.path)).toEqual(["/capabilities/habits/install", "/capabilities/habits"]);
  expect(bindings).toEqual({ knowledge_ids: ["knowledge-existing"], skill_ids: ["manual", "meta"], mcp_ids: ["mcp-existing"] });
});

test("candidate confirmation, native skill toggle refresh, forgetting and fresh relearning", async ({ page }) => {
  const { panel, requests } = await setup(page, candidate());
  await expect(panel).toContainText("2/3 条有效证据");
  await panel.getByRole("button", { name: "纠正或采纳", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "纠正或采纳习惯", exact: true });
  await dialog.getByLabel("习惯更正值").selectOption("en");
  await dialog.getByRole("button", { name: "确认此习惯", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(requests.find(item => item.path.endsWith("/correct"))?.body).toEqual({ value: "en", revision: 4 });
  await expect(panel).toContainText("已由你直接确认");
  const card = page.locator(".capability-card").filter({ has: page.getByRole("heading", { name: /learned-language/ }) });
  await card.getByRole("button", { name: "停用", exact: true }).click();
  await expect(panel).toContainText("技能已停用");
  await panel.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(panel.getByRole("button", { name: "暂停", exact: true })).toBeEnabled();
  await panel.getByRole("button", { name: "遗忘", exact: true }).click();
  await expect(panel).toContainText("已遗忘，旧证据不会再生");
  await panel.getByRole("button", { name: "重新学习", exact: true }).click();
  await expect(panel).toContainText("0/3 条有效证据");
});

test("stale correction is rejected and a reopened editor uses the latest revision", async ({ page }) => {
  const { panel, requests } = await setup(page, candidate(), false, true);
  await panel.getByRole("button", { name: "纠正或采纳", exact: true }).click();
  await page.getByRole("dialog", { name: "纠正或采纳习惯" }).getByRole("button", { name: "确认此习惯", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("记录已更新");
  await expect(panel.getByRole("button", { name: "纠正或采纳", exact: true })).toBeEnabled();
  await panel.getByRole("button", { name: "纠正或采纳", exact: true }).click();
  await page.getByRole("dialog", { name: "纠正或采纳习惯" }).getByRole("button", { name: "确认此习惯", exact: true }).click();
  await expect.poll(() => requests.filter(item => item.path.endsWith("/correct")).map(item => item.body)).toEqual([{ value: "zh-CN", revision: 4 }, { value: "zh-CN", revision: 8 }]);
});

test("read-only viewers have no mutation controls and the phone panel fits", async ({ page }) => {
  const { panel, requests } = await setup(page, candidate(), true);
  await expect(panel.getByLabel("开启习惯学习")).toBeDisabled();
  await expect(panel.getByRole("button", { name: "纠正或采纳", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("导入技能文件")).toHaveCount(0);
  const cards = page.locator(".capability-card");
  await expect(cards).not.toHaveCount(0);
  await expect(cards.getByRole("checkbox").first()).toBeDisabled();
  await expect(cards.getByRole("button", { name: /^(停用|启用|删除)$/ })).toHaveCount(0);
  await cards.getByRole("button", { name: "查看资源", exact: true }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "关闭技能资源", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(requests.filter(item => item.method !== "GET")).toEqual([]);
});

test("source navigation opens the authorized original conversation", async ({ page }) => {
  const { panel } = await setup(page, candidate());
  await panel.getByRole("button", { name: "查看来源", exact: true }).click();
  await page.getByRole("dialog", { name: "习惯来源：回复语言", exact: true }).getByRole("button", { name: "打开来源会话", exact: true }).click();
  await expect(panel).not.toBeVisible();
  await expect(page.getByText("以后默认用中文回复，这是来源消息。", { exact: true })).toBeVisible();
});

test("pausing and resuming an ungenerated candidate keeps its evidence below promotion", async ({ page }) => {
  const { panel, requests } = await setup(page, candidate());
  await panel.getByRole("button", { name: "暂停", exact: true }).click();
  await expect(panel).toContainText("已暂停");
  await panel.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(panel).toContainText("正在积累证据");
  await expect(panel).toContainText("2/3 条有效证据");
  await expect(panel.getByRole("button", { name: "查看生成技能", exact: true })).toHaveCount(0);
  expect(requests.filter(item => item.method === "PATCH").map(item => item.body)).toEqual([{ enabled: false, revision: 4 }, { enabled: true, revision: 5 }]);
});

test("generated skills with insufficient live sources are labeled unavailable", async ({ page }) => {
  const { panel } = await setup(page, { ...candidate(), status: "active", skill_id: "child", skill_version: 1, skill_enabled: true });
  await expect(panel).toContainText("2/3 条有效证据");
  await expect(panel).toContainText("来源不足，暂不可用");
});
