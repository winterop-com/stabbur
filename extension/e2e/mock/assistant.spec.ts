// GET /api/assistant: a 404 shows no target banner; a 200 with can_verify shows
// the banner + Verify, and the verified-ok / verified-error outcomes render.

import { test, expect, openPanel, seedSettings } from "../fixtures";

import { HeimMock, TargetSiteMock } from "../mockServer";

const mock = new HeimMock();
const target = new TargetSiteMock();

test.beforeAll(async () => {
  await mock.start();
  await target.start();
});
test.afterAll(async () => {
  await mock.stop();
  await target.stop();
});
test.beforeEach(() => {
  mock.reset();
  mock.state.phase = "ready";
  target.reset();
});

async function openReady(context: import("@playwright/test").BrowserContext, extensionId: string) {
  await seedSettings(context, extensionId, { baseUrl: mock.baseUrl(), token: "" });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  return panel;
}

test("404 assistant metadata: no target banner, chat still works", async ({ context, extensionId }) => {
  mock.state.assistant = "missing";
  const panel = await openReady(context, extensionId);
  await expect(panel.getByRole("button", { name: "Verify" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Who am I here?" })).toHaveCount(0);
});

test("verify ok renders the assistant banner and a verified indicator", async ({ context, extensionId }) => {
  mock.state.assistant = {
    name: "play42",
    base_url: mock.baseUrl(),
    auth: "basic",
    readonly: true,
    source: "d2w profile play42",
    can_verify: true,
    verified: null,
  };
  mock.state.assistantVerified = {
    ok: true,
    data: { status: "connected", version: "2.42" },
    error: null,
    checked_at: 1_700_000_000,
  };

  const panel = await openReady(context, extensionId);
  await expect(panel.getByText("play42", { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText("read-only")).toBeVisible();

  await panel.getByRole("button", { name: "Verify" }).click();
  await expect(panel.getByText("Verified.")).toBeVisible({ timeout: 10_000 });
});

test("no probe declared: no 'Who am I here?' button", async ({ context, extensionId }) => {
  mock.state.assistant = {
    name: "play42",
    base_url: mock.baseUrl(),
    auth: "basic",
    readonly: true,
    can_verify: true,
    verified: null,
  };
  const panel = await openReady(context, extensionId);
  await expect(panel.getByText("play42", { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByRole("button", { name: "Who am I here?" })).toHaveCount(0);
});

test("probe declared: the button resolves the signed-in user on the target tab", async ({ context, extensionId }) => {
  mock.state.assistant = {
    name: "play42",
    base_url: target.baseUrl(),
    auth: "basic",
    readonly: true,
    can_verify: false,
    verified: null,
    probe: {
      paths: ["/api/me.json?fields=name,username", "/api/system/info.json"],
      fields: {
        username: ["0.username"],
        name: ["0.name"],
        version: ["1.version"],
        instanceName: ["1.systemName", "1.instanceName"],
      },
      label: "Browsing as {name} on {instanceName} ({version})",
    },
  };
  const panel = await openReady(context, extensionId);
  await expect(panel.getByRole("button", { name: "Who am I here?" })).toBeVisible({ timeout: 15_000 });

  const tab = await context.newPage();
  await tab.goto(`${target.baseUrl()}/dhis`);
  await tab.bringToFront();

  await panel.getByRole("button", { name: "Who am I here?" }).click();
  await expect(panel.getByText(/Admin User/)).toBeVisible({ timeout: 15_000 });
  await tab.close();
});

test("verify error renders the failure detail", async ({ context, extensionId }) => {
  mock.state.assistant = {
    name: "play42",
    base_url: mock.baseUrl(),
    auth: "basic",
    readonly: true,
    can_verify: true,
    verified: null,
  };
  mock.state.assistantVerified = {
    ok: false,
    data: null,
    error: "profile verify failed: 401",
    checked_at: 1_700_000_000,
  };

  const panel = await openReady(context, extensionId);
  await panel.getByRole("button", { name: "Verify" }).click();
  await expect(panel.getByText(/Verification failed: profile verify failed: 401/)).toBeVisible({
    timeout: 10_000,
  });
});
