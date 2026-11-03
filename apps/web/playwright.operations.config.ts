import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// Explicit maintenance acceptance: this suite briefly stops only the local platform services.
export default defineConfig({
  ...base,
  testIgnore: [],
  testMatch: "**/operations-backup-native.spec.ts",
});
