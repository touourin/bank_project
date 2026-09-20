import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:5174",
    channel: "chrome",
    colorScheme: "light",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 980 },
  },
  webServer: [
    {
      command:
        ".venv/bin/python -m uvicorn frontend_server:app_factory --factory --app-dir tests --host 127.0.0.1 --port 8011",
      cwd: "..",
      url: "http://127.0.0.1:8011/health",
      reuseExistingServer: false,
    },
    {
      command: "npm run dev -- --port 5174",
      url: "http://127.0.0.1:5174",
      env: { BANK_API_PROXY: "http://127.0.0.1:8011" },
      reuseExistingServer: false,
    },
  ],
});
