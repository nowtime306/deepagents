import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// Run start, restart the verified local platform services, then run resume.
export default defineConfig({ ...base, testIgnore: [], testMatch: "**/workflow-task-*.spec.ts" });
