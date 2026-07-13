// Multi-target registry in the panel: GET /api/assistants returns >1 target, the panel selects one
// per the active tab (client-side `selectTarget`), auto-switches on navigation, resolves a same-base
// tie with a picker, carries the selected target id on each chat turn, and keeps per-target bindings
// isolated (bind A, switch to B -> B unbound). The single-assistant 404-compat mode is exercised by the
// existing assistant.spec / bind.spec / tabmatch.spec, which keep passing unchanged.

import { test, expect, openPanel, seedSettings } from "../fixtures";
import { HeimMock, TargetSiteMock, bindAssistant, bindAssistantTarget } from "../mockServer";
import type { BrowserContext, Page } from "@playwright/test";

const heim = new HeimMock();
const siteA = new TargetSiteMock();
const siteB = new TargetSiteMock();

test.beforeAll(async () => {
  await heim.start();
  await siteA.start();
  await siteB.start();
});
test.afterAll(async () => {
  await heim.stop();
  await siteA.stop();
  await siteB.stop();
});
test.beforeEach(() => {
  heim.reset();
  heim.state.phase = "ready";
  siteA.reset();
  siteB.reset();
});

const MATCH_TEXT = "This tab matches the assistant target.";

/** A minimal registry entry (no probe / bind recipe), enough to drive selection + the chat body. */
function simpleTarget(id: string, name: string, baseUrl: string): Record<string, unknown> {
  return { id, name, base_url: baseUrl, mcp_servers: [id], can_verify: false, verified: null };
}

async function openReady(context: BrowserContext, extensionId: string): Promise<Page> {
  await seedSettings(context, extensionId, { baseUrl: heim.baseUrl(), token: "" });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  return panel;
}

async function sendChat(panel: Page, text: string): Promise<void> {
  await panel.getByPlaceholder(/Message \(Enter to send/).fill(text);
  await panel.getByRole("button", { name: "Send" }).click();
}

/** The `target` field of the most recent POST /api/chat body the mock recorded. */
function lastChatTarget(): unknown {
  const raw = heim.state.chatRequests.at(-1);
  return raw ? (JSON.parse(raw) as { target?: unknown }).target : undefined;
}

test("distinct targets: tab selects one, navigation auto-switches, chat carries its id", async ({
  context,
  extensionId,
}) => {
  heim.state.assistants = [
    simpleTarget("alpha", "Alpha", siteA.baseUrl()),
    simpleTarget("beta", "Beta", siteB.baseUrl()),
  ];
  const panel = await openReady(context, extensionId);

  const tab = await context.newPage();
  await tab.goto(`${siteA.baseUrl()}/dhis`);
  await tab.bringToFront();

  // Tab under A's base -> A is selected (only A matches), no picker.
  await expect(panel.getByText(MATCH_TEXT)).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText("Alpha", { exact: true })).toBeVisible();
  await expect(panel.getByTestId("target-picker")).toHaveCount(0);

  await sendChat(panel, "hello A");
  await expect.poll(() => heim.state.chatRequests.length).toBeGreaterThan(0);
  expect(lastChatTarget()).toBe("alpha");

  // Navigate the same tab under B's origin -> the panel auto-switches to B.
  await tab.goto(`${siteB.baseUrl()}/dhis`);
  await tab.bringToFront();
  await expect(panel.getByText("Beta", { exact: true })).toBeVisible({ timeout: 15_000 });

  await sendChat(panel, "hello B");
  await expect.poll(() => heim.state.chatRequests.length).toBeGreaterThan(1);
  expect(lastChatTarget()).toBe("beta");
  await tab.close();
});

test("same-base tie: a picker appears and choosing one drives banner + chat", async ({ context, extensionId }) => {
  heim.state.assistants = [
    simpleTarget("alpha", "Alpha", siteA.baseUrl()),
    simpleTarget("beta", "Beta", siteA.baseUrl()), // identical base_url -> ambiguous
  ];
  const panel = await openReady(context, extensionId);

  const tab = await context.newPage();
  await tab.goto(`${siteA.baseUrl()}/dhis`);
  await tab.bringToFront();

  // Both match equally -> no auto-selection: the picker stands in and the banner holds off.
  await expect(panel.getByTestId("target-picker")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText(MATCH_TEXT)).toHaveCount(0);

  // Choosing a target resolves the tie: the banner appears (acting on the pick) and the picker holds it.
  await panel.getByTestId("target-picker").selectOption("beta");
  await expect(panel.getByText(MATCH_TEXT)).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("target-picker")).toHaveValue("beta");

  await sendChat(panel, "pick beta");
  await expect.poll(() => heim.state.chatRequests.length).toBeGreaterThan(0);
  expect(lastChatTarget()).toBe("beta");
  await tab.close();
});

test("per-target binding isolation: bind A, switch to B (unbound), switch back to A (bound)", async ({
  context,
  extensionId,
}) => {
  heim.state.assistants = [
    bindAssistantTarget("alpha", siteA.baseUrl(), { name: "Alpha" }),
    bindAssistantTarget("beta", siteB.baseUrl(), { name: "Beta" }),
  ];
  const panel = await openReady(context, extensionId);

  const tab = await context.newPage();
  await tab.goto(`${siteA.baseUrl()}/dhis`);
  await tab.bringToFront();

  // A is selected + logged in + unbound -> the auto-offer pops. Confirm -> bound on A.
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();
  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  const bindCall = heim.state.bindCalls.find((c) => c.endpoint === "bind");
  expect(bindCall?.targetId).toBe("alpha");

  // Switch the tab to B's origin -> auto-switch to B. B has no binding of its own: no acting-as chip,
  // and the auto-offer is eligible again (its own consent card).
  await tab.goto(`${siteB.baseUrl()}/dhis`);
  await tab.bringToFront();
  await expect(panel.getByText("Beta", { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("bind-acting-as")).toHaveCount(0);
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });

  // Back to A -> A's binding (composite key) is still there: the acting-as chip returns.
  await tab.goto(`${siteA.baseUrl()}/dhis`);
  await tab.bringToFront();
  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await tab.close();
});

test("single-assistant 404-compat: registry endpoint 404 falls back to /api/assistant", async ({
  context,
  extensionId,
}) => {
  // No registry, but the old single-assistant route answers -> the panel wraps it as a one-target
  // registry and binds/verifies via the compat /api/assistant* routes.
  heim.state.assistants = null;
  heim.state.assistant = bindAssistant(siteA.baseUrl());
  const panel = await openReady(context, extensionId);

  const tab = await context.newPage();
  await tab.goto(`${siteA.baseUrl()}/dhis`);
  await tab.bringToFront();
  await expect(panel.getByText(MATCH_TEXT)).toBeVisible({ timeout: 15_000 });

  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();
  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  // Compat mode uses the un-scoped route: the bind call carries no target id.
  const bindCall = heim.state.bindCalls.find((c) => c.endpoint === "bind");
  expect(bindCall).toBeTruthy();
  expect(bindCall?.targetId).toBeUndefined();

  await sendChat(panel, "compat hi");
  await expect.poll(() => heim.state.chatRequests.length).toBeGreaterThan(0);
  // A single compat target still narrows the chat to its derived id (slugified "play42").
  expect(lastChatTarget()).toBe("play42");
  await tab.close();
});
