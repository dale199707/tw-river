import {defineConfig} from "@playwright/test";

export default defineConfig({
  testDir: "tests",
  timeout: 30000,
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? "github" : "line",
  use: {
    baseURL: "http://127.0.0.1:8766",
    browserName: "chromium",
    channel: process.env.CI ? undefined : "chrome",
    headless: true,
  },
  webServer: {
    command: "python3 -m http.server 8766 --bind 127.0.0.1",
    url: "http://127.0.0.1:8766",
    reuseExistingServer: !process.env.CI,
    timeout: 120000,
  },
});
