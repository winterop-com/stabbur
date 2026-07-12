// Page-text capture: with page context + page text enabled, the visible page
// text reaches the POST /api/chat request body under the "Page text (truncated):"
// label. With page text off, the label is absent. The mock records request bodies.

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

/** Open a content tab on the mock origin (host permission covers 127.0.0.1),
 *  inject a distinctive body, and focus it so it is the panel's active tab.
 *  Open this AFTER the panel so the content tab is the last-focused tab (the
 *  side panel is a real tab in the e2e harness, so tab ordering matters). */
async function openContentTab(
  context: import("@playwright/test").BrowserContext,
  marker: string,
): Promise<import("@playwright/test").Page> {
  const tab = await context.newPage();
  await tab.goto(`${mock.baseUrl()}/some/article`);
  await tab.evaluate((m) => {
    document.body.innerHTML = `<h1>${m}</h1><p>${m} body paragraph with plenty of readable text.</p>`;
  }, marker);
  await tab.bringToFront();
  return tab;
}

async function sendAndWait(panel: import("@playwright/test").Page): Promise<void> {
  await panel.getByPlaceholder(/Message \(Enter to send/).fill("summarize this page");
  await panel.getByRole("button", { name: "Send" }).click();
  // Default frames stream a single "ok" token; wait for it so the POST completed.
  await expect(panel.getByText("ok").last()).toBeVisible({ timeout: 15_000 });
}

test("page text reaches the /api/chat body when enabled", async ({ context, extensionId }) => {
  await seedSettings(context, extensionId, {
    baseUrl: mock.baseUrl(),
    token: "",
    pageContextEnabled: true,
    pageTextEnabled: true,
  });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  const marker = "MARKER_PAGETEXT_ON";
  const tab = await openContentTab(context, marker);

  await sendAndWait(panel);
  await tab.close();

  const bodies = mock.state.chatRequests;
  expect(bodies.length).toBeGreaterThan(0);
  const last = bodies[bodies.length - 1];
  const parsed = JSON.parse(last) as { messages: { role: string; content: string }[] };
  const userContent = parsed.messages.map((m) => m.content).join("\n");
  expect(userContent).toContain("Page text (truncated):");
  expect(userContent).toContain(marker);
});

test("probe + tool account identities reach the /api/chat body", async ({ context, extensionId }) => {
  // A probe-declaring assistant pointed at the signed-in target site: the chat turn's context
  // carries both the browser session user (from the probe) and the tool account (from metadata),
  // each explicitly labeled so the model treats them as distinct identities.
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
    },
  };
  await seedSettings(context, extensionId, {
    baseUrl: mock.baseUrl(),
    token: "",
    pageContextEnabled: true,
    pageTextEnabled: false,
  });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });

  const tab = await context.newPage();
  await tab.goto(`${target.baseUrl()}/dhis`);
  await tab.bringToFront();
  await expect(panel.getByRole("button", { name: "Who am I here?" })).toBeVisible({ timeout: 15_000 });

  await sendAndWait(panel);
  await tab.close();

  const bodies = mock.state.chatRequests;
  expect(bodies.length).toBeGreaterThan(0);
  const last = bodies[bodies.length - 1];
  expect(last).toContain("Browser session user:");
  expect(last).toContain("Tool account:");
});

test("page text is omitted when the toggle is off", async ({ context, extensionId }) => {
  await seedSettings(context, extensionId, {
    baseUrl: mock.baseUrl(),
    token: "",
    pageContextEnabled: true,
    pageTextEnabled: false,
  });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  const marker = "MARKER_PAGETEXT_OFF";
  const tab = await openContentTab(context, marker);

  await sendAndWait(panel);
  await tab.close();

  const bodies = mock.state.chatRequests;
  expect(bodies.length).toBeGreaterThan(0);
  const last = bodies[bodies.length - 1];
  // Page URL/title still present (page context on), but no page-text block.
  expect(last).toContain("Page URL:");
  expect(last).not.toContain("Page text (truncated):");
  expect(last).not.toContain(marker);
});
