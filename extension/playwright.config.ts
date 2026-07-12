import { defineConfig } from "@playwright/test";

// Two tiers, selected by --project:
//   mock  - fast, hermetic; drives the panel against an in-process heim API mock.
//   live  - end-to-end against a real `heim serve` + the public DHIS2 play demo.
//
// Extensions require a persistent context and cannot share one across parallel
// workers cleanly, so we pin workers to 1. The extension is loaded headless via
// Chromium's new headless mode (channel "chromium"); export HEADED=1 to force a
// visible window on a machine that can't run headless extensions.
export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "mock",
      testDir: "e2e/mock",
      timeout: 60_000,
      retries: 0,
    },
    {
      name: "live",
      testDir: "e2e/live",
      timeout: 900_000,
      retries: 0,
    },
    {
      // Prompt-catalog specs: format-parity unit + the UI spot-check driving a real
      // heim. NOT part of the default `e2e` (mock) run. The batch verification
      // harness itself is `bun run e2e/prompts/run.ts` (not a Playwright test).
      name: "prompts",
      testDir: "e2e/prompts",
      timeout: 900_000,
      retries: 0,
    },
  ],
});
