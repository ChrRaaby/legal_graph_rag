import { defineConfig, devices } from "@playwright/test";

// The server (uvicorn serving the built dist) must be running on :8000 before
// `npx playwright test` — this smoke test drives the real UI + a real agent run.
export default defineConfig({
  testDir: "./tests",
  testMatch: "**/*.spec.ts",
  timeout: 90_000,
  use: {
    baseURL: "http://127.0.0.1:8000",
    ...devices["Desktop Chrome"],
  },
  reporter: [["list"]],
});
