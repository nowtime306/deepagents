import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("actual route receiver consent, immutable price and personal budget edit fit narrow layout", async ({
  page,
}) => {
  const origin = "http://localhost:3100";
  const fixture = JSON.parse(
    readFileSync(
      resolve("../../services/api/.local/routing-billing-fixture.json"),
      "utf8",
    ),
  );
  await page.goto(origin);
  await page.getByLabel("账号", { exact: true }).fill(fixture.username);
  await page.getByLabel("密码", { exact: true }).fill(fixture.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  for (const role of ["primary", "backup"]) {
    const response = await page.request.post(`${origin}/api/v1/admin/models`, {
      headers: { Origin: origin },
      data: {
        name: `${fixture.prefix}-${role}`,
        model: "local-unused",
        base_url: `http://${role}.invalid/v1`,
        supports_tools: true,
      },
    });
    expect(response.status()).toBe(201);
  }
  await page.reload();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const routes = page.getByRole("region", {
    name: "模型路由设置",
    exact: true,
  });
  await routes.getByRole("button", { name: "新增路由", exact: true }).click();
  await routes
    .getByLabel("路由名称", { exact: true })
    .fill(`${fixture.prefix}-route`);
  await routes
    .getByRole("combobox", { name: "主模型", exact: true })
    .selectOption({ label: `${fixture.prefix}-primary` });
  await routes
    .getByRole("combobox", { name: "按顺序添加备用模型", exact: true })
    .selectOption({ label: `${fixture.prefix}-backup` });
  await routes.getByRole("button", { name: "保存路由", exact: true }).click();
  await expect(routes).toContainText("http://primary.invalid");
  await expect(routes).toContainText("http://backup.invalid");
  const card = routes
    .locator("article")
    .filter({ hasText: `${fixture.prefix}-route` });
  page.once("dialog", (dialog) => dialog.accept());
  await card
    .getByRole("button", { name: "确认候选接收方", exact: true })
    .click();
  await expect(card).toContainText("已同意当前接收方");
  await card.getByRole("button", { name: "撤销同意", exact: true }).click();
  await expect(card).toContainText("当前接收方尚未确认");
  await page.getByRole("button", { name: "运营管理", exact: true }).click();
  const billing = page.getByRole("region", { name: "费用与预算", exact: true });
  await billing.getByRole("button", { name: "新增价格版本", exact: true }).click();
  await billing
    .getByRole("combobox", { name: "定价模型", exact: true })
    .selectOption({ label: `${fixture.prefix}-primary` });
  await billing.getByLabel("每百万普通输入价格", { exact: true }).fill("2");
  await billing.getByLabel("每百万输出价格", { exact: true }).fill("4");
  await billing
    .getByLabel("价格来源与核对说明", { exact: true })
    .fill("本机页面测试价格，不代表真实报价");
  await billing
    .getByRole("button", { name: "新增不可变价格版本", exact: true })
    .click();
  await expect(billing.getByRole("status")).toContainText("已新增价格版本");
  await billing.getByRole("button", { name: "新增预算", exact: true }).click();
  await billing.getByLabel("周期金额", { exact: true }).fill("10");
  await billing.getByLabel("启用个人金额预算", { exact: true }).check();
  await billing
    .getByRole("button", { name: "保存个人预算", exact: true })
    .click();
  await expect(billing).toContainText("上限 10.000000 / 30 天");
  const capacity = page.getByRole("region", {
    name: "备份与容量",
    exact: true,
  });
  await expect(capacity).toContainText("本机观测");
  await expect(capacity).toContainText("磁盘剩余");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await billing.evaluate((e) => e.scrollWidth <= e.clientWidth + 1),
  ).toBe(true);
  expect(
    await capacity.evaluate((e) => e.scrollWidth <= e.clientWidth + 1),
  ).toBe(true);
  await page.screenshot({
    path: "../../.local/phase8-billing-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await routes.evaluate((e) => e.scrollWidth <= e.clientWidth + 1)).toBe(
    true,
  );
  const result = await page.request.get(`${origin}/api/v1/model-routes`);
  expect(await result.text()).not.toContain("api_key");
  await routes.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "../../.local/phase8-routes-mobile.png",
    fullPage: true,
  });
});
