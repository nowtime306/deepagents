import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests",
  testIgnore: ["**/workflow-task-start.spec.ts", "**/workflow-task-resume.spec.ts", "**/operations-backup-native.spec.ts"],
  outputDir: "test-results/artifacts",
  use: {
    baseURL: "http://127.0.0.1:3100",
    channel: "chrome",
    viewport: { width: 1440, height: 900 },
  },
  workers: 1,
  reporter: "list",
});
