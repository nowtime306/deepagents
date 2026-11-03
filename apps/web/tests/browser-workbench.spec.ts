import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const origin = "http://localhost:3100";

test("native browser viewing, exclusive manual input, safe close and mobile layout", async ({
  page,
}) => {
  test.setTimeout(90000);
  const fixture = JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        "../../services/api/.local/browser-ui-fixture.json",
      ),
      "utf8",
    ),
  );
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
  await page.getByRole("button", { name: "任务工作台", exact: true }).click();
  const card = page
    .locator(".run-history-card")
    .filter({ hasText: fixture.name });
  await card.getByRole("button", { name: "执行详情", exact: true }).click();
  const workbench = page.getByRole("region", {
    name: "浏览器工作台",
    exact: true,
  });
  await expect(workbench.getByAltText("任务浏览器当前画面")).toBeVisible({
    timeout: 20000,
  });
  await workbench
    .getByRole("button", { name: "接管浏览器", exact: true })
    .click();
  await expect(
    workbench.getByText("人工控制中", { exact: true }),
  ).toBeVisible();
  const frame = workbench.getByRole("button", {
    name: "点击浏览器画面",
    exact: true,
  });
  await expect(frame).toBeEnabled();
  const blocked = await page.request.post(
    `${origin}/api/v1/work-runs/${fixture.run}/resume`,
    { headers: { Origin: origin } },
  );
  expect(blocked.status()).toBe(409);
  await workbench
    .getByLabel("授权网页地址", { exact: true })
    .fill("https://example.com/owner-fixture");
  await workbench
    .getByRole("button", { name: "打开网页", exact: true })
    .click();
  await expect(
    workbench.getByRole("button", { name: "切换焦点", exact: true }),
  ).toBeEnabled();
  // Tab focuses the controlled page's first input without sending screen coordinates.
  await workbench
    .getByRole("button", { name: "切换焦点", exact: true })
    .click();
  await expect(
    workbench.getByRole("button", { name: "输入到网页", exact: true }),
  ).toBeDisabled();
  await workbench
    .getByLabel("发送到当前焦点", { exact: true })
    .fill("本机接管验证通过");
  await workbench
    .getByRole("button", { name: "输入到网页", exact: true })
    .click();
  await expect(
    workbench.getByLabel("发送到当前焦点", { exact: true }),
  ).toHaveValue("");
  await expect(
    workbench.getByRole("button", { name: "按 Enter", exact: true }),
  ).toBeEnabled();
  await workbench
    .getByRole("button", { name: "按 Enter", exact: true })
    .click();
  await expect(
    workbench.getByRole("button", { name: "结束控制并保存", exact: true }),
  ).toBeEnabled();
  await workbench
    .getByRole("button", { name: "结束控制并保存", exact: true })
    .click();
  await expect(
    workbench.getByRole("button", { name: "接管浏览器", exact: true }),
  ).toBeVisible({ timeout: 20000 });
  const summary = await (
    await page.request.get(`${origin}/api/v1/work-runs/${fixture.run}/browser`)
  ).json();
  expect(summary.login.has_saved_state).toBeTruthy();
  expect(summary.control.status).toBe("CLOSED");
  await expect(frame).toHaveCSS("opacity", "1");
  expect(summary.actions).toHaveLength(4);
  expect(
    summary.actions.every(
      (item: { status: string }) => item.status === "SUCCEEDED",
    ),
  ).toBeTruthy();
  expect(JSON.stringify(summary)).not.toContain("private-fixture");
  expect(JSON.stringify(summary)).not.toContain("本机接管验证通过");
  expect(JSON.stringify(summary)).not.toMatch(
    /token_ciphertext|lease_token|state_ciphertext|endpoint/,
  );
  await workbench.screenshot({
    path: "../../.local/browser-workbench-desktop.png",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await workbench.scrollIntoViewIfNeeded();
  await workbench
    .getByRole("button", { name: "原始尺寸", exact: true })
    .click();
  await expect(workbench.locator(".browser-frame")).toHaveCSS(
    "width",
    "1280px",
  );
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    )
    .toBeTruthy();
  await workbench.screenshot({
    path: "../../.local/browser-workbench-mobile.png",
  });
  await page.route(`**/api/v1/work-runs/${fixture.run}/browser`, (route) =>
    route.fulfill({
      json: {
        ...summary,
        session: { state: "RELEASED", transport_ready: false, data_lost: true },
      },
    }),
  );
  await expect(
    workbench.getByText(
      "环境已过期，未保存的变更可能丢失，请先核对运行记录。",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(workbench.getByAltText("任务浏览器当前画面")).toHaveCount(0);
});
