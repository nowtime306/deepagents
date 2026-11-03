import { expect, test } from "@playwright/test";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

test("actual idle backup archives source, restores isolated DB and restarts the original services", async ({
  page,
}) => {
  test.setTimeout(180000);
  const origin = "http://localhost:3100";
  const values = Object.fromEntries(
    readFileSync(resolve("../../services/api/.env.admin"), "utf8")
      .trim()
      .split(/\r?\n/)
      .map((line) => {
        const i = line.indexOf("=");
        return [line.slice(0, i), line.slice(i + 1)];
      }),
  );
  await page.goto(origin);
  await page.getByLabel("账号", { exact: true }).fill(values.ADMIN_USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(values.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: "运营管理", exact: true }).click();
  const panel = page.getByRole("region", { name: "备份与容量", exact: true });
  await expect(panel).toContainText("本机观测");
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/operations/backups/jobs") &&
      r.request().method() === "POST",
  );
  page.once("dialog", (dialog) => dialog.accept());
  await panel
    .getByRole("button", { name: "立即排队备份", exact: true })
    .click();
  const admission = await response;
  expect(admission.status()).toBe(202);
  const id = (await admission.json()).id;
  const metadata = resolve("../../.local/operations-backup-test.json");
  writeFileSync(metadata, JSON.stringify({ id, status: "PENDING" }, null, 2));
  let result:
    | {
        id: string;
        status: string;
        reason: string | null;
        backup_name: string;
        verification: {
          records_match: boolean;
          files_verified: number;
          referenced_files_verified: number;
        };
      }
    | undefined;
  await expect
    .poll(
      async () => {
        try {
          const response = await page.request.get(
            `${origin}/api/v1/operations/backups`,
            { timeout: 5000 },
          );
          if (!response.ok()) return "OFFLINE";
          result = (await response.json()).items.find(
            (job: { id: string }) => job.id === id,
          );
          return result && ["SUCCEEDED", "FAILED"].includes(result.status)
            ? "FINISHED"
            : "WAITING";
        } catch {
          return "OFFLINE";
        }
      },
      { timeout: 150000, intervals: [1000, 2000, 5000] },
    )
    .toBe("FINISHED");
  writeFileSync(metadata, JSON.stringify(result, null, 2));
  expect(result?.status, result?.reason || "Backup did not complete").toBe(
    "SUCCEEDED",
  );
  expect(result?.verification.records_match).toBe(true);
  const directory = resolve(
    "../../services/api/.local/backups",
    result!.backup_name,
  );
  expect(existsSync(resolve(directory, "source.zip"))).toBe(true);
  const source = JSON.parse(
    readFileSync(resolve(directory, "source-info.json"), "utf8"),
  );
  expect(source.working_copy).toBe(true);
  expect(source.branch).toBe("onwer_agent");
  expect(source.files).toBeGreaterThan(100);
  await expect(
    page
      .getByRole("region", { name: "平台后台状态", exact: true })
      .getByText(/1个实例在线$/),
  ).toHaveCount(4, { timeout: 20000 });
  await expect(panel).toContainText(result!.backup_name, { timeout: 10000 });
  await page.screenshot({
    path: "../../.local/phase8-backup-accepted.png",
    fullPage: true,
  });
});
