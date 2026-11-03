import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const origin = "http://localhost:3100";
const apiPath = resolve("../../services/api");
const metadata = resolve(apiPath, ".local/reference-browser-fixture.json");
function fixture(action: string) {
  execFileSync(resolve(apiPath, ".venv/Scripts/python.exe"), ["-m", "tests.manual.reference_browser_fixture", action],
    { cwd: apiPath, windowsHide: true, stdio: "pipe" });
}

test("private conversation references persist, refresh explicitly, estimate tokens and fit mobile", async ({ page }) => {
  test.setTimeout(60000);
  fixture("create");
  const recorded = JSON.parse(readFileSync(metadata, "utf8"));
  const credentials = Object.fromEntries(readFileSync(resolve(apiPath, ".env.admin"), "utf8").trim().split(/\r?\n/)
    .map((line) => { const index = line.indexOf("="); return [line.slice(0, index), line.slice(index + 1)]; }));
  try {
    await page.goto(origin);
    await page.getByLabel("账号", { exact: true }).fill(credentials.ADMIN_USERNAME);
    await page.getByLabel("密码", { exact: true }).fill(credentials.ADMIN_PASSWORD);
    await page.getByRole("button", { name: "进入工作台" }).click();
    await page.getByRole("button", { name: "新建对话", exact: true }).click();
    const created = page.waitForResponse((response) => response.url().endsWith("/api/v1/conversations")
      && response.request().method() === "POST");
    await page.getByRole("button", { name: "引用其他会话", exact: true }).click();
    recorded.target = (await (await created).json()).id;
    writeFileSync(metadata, JSON.stringify(recorded));
    const panel = page.locator("details.conversation-references");
    await panel.locator("summary").click();
    await page.getByLabel("搜索本人会话").fill(recorded.label);
    await page.getByRole("button", { name: "查找来源", exact: true }).click();
    await page.getByLabel("选择引用来源").selectOption(recorded.sources[0]);
    await page.getByRole("button", { name: "添加引用", exact: true }).click();
    await expect(panel.getByText("1条正文 · 9字", { exact: true })).toBeVisible();
    fixture("append");
    const before = await (await page.request.get(`${origin}/api/v1/conversations/${recorded.target}/references`)).json();
    expect(before.items[0].message_count).toBe(1);
    await page.getByRole("button", { name: `更新引用 ${recorded.label}0`, exact: true }).click();
    await expect(panel.locator(".reference-source small")).toContainText("2条正文");
    await page.getByLabel("选择引用来源").selectOption(recorded.sources[1]);
    await page.getByRole("button", { name: "添加引用", exact: true }).click();
    await expect(panel.locator(".reference-source")).toHaveCount(2);
    await page.getByLabel("发送消息", { exact: true }).fill("参考引用资料，暂不发送");
    await expect(page.locator(".token-estimate summary")).toContainText("预计输入");
    await page.locator(".token-estimate summary").click();
    await expect(page.getByText(/其中引用会话预计输入约/)).toBeVisible();
    await page.getByRole("button", { name: `移除引用 ${recorded.label}0`, exact: true }).click();
    await expect(panel.locator(".reference-source")).toHaveCount(1);
    await page.reload();
    await page.getByRole("button", { name: "引用资料的新对话", exact: true }).click();
    await panel.locator("summary").click();
    await expect(panel.getByRole("strong")).toHaveText(`${recorded.label}1`);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(panel).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await panel.screenshot({ path: resolve("../../.local/conversation-references-mobile.png") });
    const usage = await (await page.request.get(`${origin}/api/v1/conversations/${recorded.target}/usage`)).json();
    expect(usage.total.calls).toBe(0);
  } finally { fixture("cleanup"); }
});
