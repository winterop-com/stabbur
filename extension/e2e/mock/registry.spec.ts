// Multi-target registry in the panel: GET /api/assistants returns >1 target, the panel selects one
// per the active tab (client-side `selectTarget`), auto-switches on navigation, resolves a same-base
// tie with a picker, carries the selected target id on each chat turn, and keeps per-target bindings
// isolated (bind A, switch to B -> B unbound). The single-assistant 404-compat mode is exercised by the
// existing assistant.spec / bind.spec / tabmatch.spec, which keep passing unchanged.

import { test, expect, openPanel, seedSettings, PANEL_PATH } from "../fixtures";
import { TAB_MATCHED } from "../../lib/bannerText";
import { HeimMock, TargetSiteMock, bindAssistant, bindAssistantTarget } from "../mockServer";
import type { BrowserContext, Page } from "@playwright/test";

const stabbur = new HeimMock();
const siteA = new TargetSiteMock();
const siteB = new TargetSiteMock();

test.beforeAll(async () => {
  await stabbur.start();
  await siteA.start();
  await siteB.start();
});
test.afterAll(async () => {
  await stabbur.stop();
  await siteA.stop();
  await siteB.stop();
});
test.beforeEach(() => {
  stabbur.reset();
  stabbur.state.phase = "ready";
  siteA.reset();
  siteB.reset();
});

const MATCH_TEXT = TAB_MATCHED;

/** A minimal registry entry (no probe / bind recipe), enough to drive selection + the chat body. */
function simpleTarget(id: string, name: string, baseUrl: string): Record<string, unknown> {
  return { id, name, base_url: baseUrl, mcp_servers: [id], can_verify: false, verified: null };
}

// Pre-multi-target storage keys (keyed by backend alone, no target segment) — what an install from
// before the multi-target registry left behind. Seeded before the panel loads so its one-time
// migration (migrateLegacyRecords, run right after the registry resolves) adopts them.
const LEGACY_BINDING_KEY = "stabbur-ext-binding:default";
const LEGACY_STALE_KEY = "stabbur-ext-binding-stale:default";
const LEGACY_DISMISS_KEY = "stabbur-ext-binding-dismissed:default";

/** Seed a pre-multi-target binding (+ its stale flag + auto-offer dismissal) under the un-scoped legacy
 *  keys, BEFORE the panel opens. A session-mode (cookie) binding is used so the record is exactly the
 *  kind the background worker would try to route — proving migration hands it a real targetId. */
async function seedLegacyRecords(context: BrowserContext, extensionId: string, baseUrl: string): Promise<void> {
  const page = await context.newPage();
  await page.goto(`chrome-extension://${extensionId}/${PANEL_PATH}`);
  await page.evaluate((url) => {
    return chrome.storage.local.set({
      "stabbur-ext-binding:default": {
        backendId: "default",
        targetBaseUrl: url,
        mode: "session",
        username: "admin",
        name: "Admin User",
        cookieName: "JSESSIONID",
        writes: true,
      },
      "stabbur-ext-binding-stale:default": true,
      "stabbur-ext-binding-dismissed:default": { backendId: "default", targetBaseUrl: url, username: "admin" },
    });
  }, baseUrl);
  await page.close();
}

/** The full chrome.storage.local snapshot, read from the panel context. */
function readStorage(panel: Page): Promise<Record<string, unknown>> {
  return panel.evaluate(() => chrome.storage.local.get(null) as Promise<Record<string, unknown>>);
}

async function openReady(context: BrowserContext, extensionId: string): Promise<Page> {
  await seedSettings(context, extensionId, { baseUrl: stabbur.baseUrl(), token: "" });
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
  const raw = stabbur.state.chatRequests.at(-1);
  return raw ? (JSON.parse(raw) as { target?: unknown }).target : undefined;
}

test("distinct targets: tab selects one, navigation auto-switches, chat carries its id", async ({
  context,
  extensionId,
}) => {
  stabbur.state.assistants = [
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
  await expect.poll(() => stabbur.state.chatRequests.length).toBeGreaterThan(0);
  expect(lastChatTarget()).toBe("alpha");

  // Navigate the same tab under B's origin -> the panel auto-switches to B.
  await tab.goto(`${siteB.baseUrl()}/dhis`);
  await tab.bringToFront();
  await expect(panel.getByText("Beta", { exact: true })).toBeVisible({ timeout: 15_000 });

  await sendChat(panel, "hello B");
  await expect.poll(() => stabbur.state.chatRequests.length).toBeGreaterThan(1);
  expect(lastChatTarget()).toBe("beta");
  await tab.close();
});

test("same-base tie: a picker appears and choosing one drives banner + chat", async ({ context, extensionId }) => {
  stabbur.state.assistants = [
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
  await expect.poll(() => stabbur.state.chatRequests.length).toBeGreaterThan(0);
  expect(lastChatTarget()).toBe("beta");
  await tab.close();
});

test("per-target binding isolation: bind A, switch to B (unbound), switch back to A (bound)", async ({
  context,
  extensionId,
}) => {
  stabbur.state.assistants = [
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
  const bindCall = stabbur.state.bindCalls.find((c) => c.endpoint === "bind");
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
  stabbur.state.assistants = null;
  stabbur.state.assistant = bindAssistant(siteA.baseUrl());
  const panel = await openReady(context, extensionId);

  const tab = await context.newPage();
  await tab.goto(`${siteA.baseUrl()}/dhis`);
  await tab.bringToFront();
  await expect(panel.getByText(MATCH_TEXT)).toBeVisible({ timeout: 15_000 });

  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();
  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  // Compat mode uses the un-scoped route: the bind call carries no target id.
  const bindCall = stabbur.state.bindCalls.find((c) => c.endpoint === "bind");
  expect(bindCall).toBeTruthy();
  expect(bindCall?.targetId).toBeUndefined();

  await sendChat(panel, "compat hi");
  await expect.poll(() => stabbur.state.chatRequests.length).toBeGreaterThan(0);
  // A single compat target still narrows the chat to its derived id (slugified "play42").
  expect(lastChatTarget()).toBe("play42");
  await tab.close();
});

test("legacy migration: a pre-multi-target record is adopted onto the PRIMARY composite key (compat=false)", async ({
  context,
  extensionId,
}) => {
  // A real >1-target registry: alpha is the primary, so the legacy record (no target segment) must land
  // on alpha's composite keys, NOT whichever target a tab happened to select.
  stabbur.state.assistants = [
    simpleTarget("alpha", "Alpha", siteA.baseUrl()),
    simpleTarget("beta", "Beta", siteB.baseUrl()),
  ];
  await seedLegacyRecords(context, extensionId, siteA.baseUrl());
  const panel = await openReady(context, extensionId);

  // Once the registry resolves, the panel migrates the legacy record onto alpha's composite key. Poll
  // until adoption lands (the migration is async, kicked off from the registry-load effect).
  await expect
    .poll(() => panel.evaluate(() => chrome.storage.local.get("stabbur-ext-binding:default:alpha").then((r) => r["stabbur-ext-binding:default:alpha"] ?? null)))
    .not.toBeNull();

  const all = await readStorage(panel);
  // The binding is now composite-keyed, stamped with the PRIMARY targetId + compat, fields preserved.
  // A targetId-stamped composite record is exactly what the background worker's index requires (it
  // skips any record without one), so this is the background-visible shape.
  expect(all["stabbur-ext-binding:default:alpha"]).toMatchObject({
    backendId: "default",
    targetId: "alpha",
    compat: false,
    mode: "session",
    cookieName: "JSESSIONID",
    writes: true,
  });
  expect(all["stabbur-ext-binding-stale:default:alpha"]).toBe(true);
  expect(all["stabbur-ext-binding-dismissed:default:alpha"]).toMatchObject({ targetId: "alpha", username: "admin" });
  // Every legacy key is gone (migrated exactly once, then removed).
  expect("stabbur-ext-binding:default" in all).toBe(false);
  expect("stabbur-ext-binding-stale:default" in all).toBe(false);
  expect("stabbur-ext-binding-dismissed:default" in all).toBe(false);
});

test("legacy migration: compat single-assistant stamps compat=true on the derived primary id", async ({
  context,
  extensionId,
}) => {
  // 404-compat single assistant: the derived primary id is the slugified name ("play42") and the
  // migrated record must carry compat=true so unbind / re-sync use the un-scoped /api/assistant* routes.
  stabbur.state.assistants = null;
  stabbur.state.assistant = bindAssistant(siteA.baseUrl());
  await seedLegacyRecords(context, extensionId, siteA.baseUrl());
  const panel = await openReady(context, extensionId);

  await expect
    .poll(() => panel.evaluate(() => chrome.storage.local.get("stabbur-ext-binding:default:play42").then((r) => r["stabbur-ext-binding:default:play42"] ?? null)))
    .not.toBeNull();

  const all = await readStorage(panel);
  expect(all["stabbur-ext-binding:default:play42"]).toMatchObject({ targetId: "play42", compat: true });
  expect("stabbur-ext-binding:default" in all).toBe(false);
});
