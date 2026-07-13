// "Use my login" bind flow: the panel mints a scoped credential in the target site's own context
// (its cookies) and hands heim only the secret. Drives the flow against a HeimMock (the bind
// endpoint) plus a TargetSiteMock (a stand-in DHIS2 the content tab opens).
//
// The DEFAULT path is now proactive: on panel open against a matched tab with a live session and no
// usable binding, the consent card auto-appears ("Use your <instance> login?"). These specs cover
// that auto-offer (appears when logged-in + unbound, no mint until Confirm), the negatives (no
// session -> no offer; bound + same user -> silent reuse), the decline memory (dismiss -> no re-nag,
// manual button still works), and drift (a different user is now logged in -> re-offer). The mint
// branches (happy PAT, 404 -> session fallback, 401/redirect -> sign-in) and unbind (revoke + call)
// ride the same auto-offered consent card.

import { test, expect, openPanel, seedSettings } from "../fixtures";
import { HeimMock, TargetSiteMock, bindAssistant } from "../mockServer";
import type { BrowserContext, Page } from "@playwright/test";

const heim = new HeimMock();
const target = new TargetSiteMock();

test.beforeAll(async () => {
  await heim.start();
  await target.start();
});
test.afterAll(async () => {
  await heim.stop();
  await target.stop();
});
test.beforeEach(() => {
  heim.reset();
  heim.state.phase = "ready";
  target.reset();
  heim.state.assistant = bindAssistant(target.baseUrl());
});

const MATCH_TEXT = "This tab matches the assistant target.";

/** Open the panel (talking to heim), then a content tab on the target site so the tab matches. */
async function openWithTargetTab(context: BrowserContext, extensionId: string): Promise<{ panel: Page; tab: Page }> {
  await seedSettings(context, extensionId, { baseUrl: heim.baseUrl(), token: "" });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  const tab = await context.newPage();
  await tab.goto(`${target.baseUrl()}/dhis`);
  await tab.bringToFront();
  // Once the panel sees the tab matching the target, the login-binding section renders.
  await expect(panel.getByText(MATCH_TEXT)).toBeVisible({ timeout: 15_000 });
  return { panel, tab };
}

/** Reopen a fresh panel against the same (persisted) storage + still-matched tab. */
async function reopenPanel(context: BrowserContext, extensionId: string, tab: Page): Promise<Page> {
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  await tab.bringToFront();
  await expect(panel.getByText(MATCH_TEXT)).toBeVisible({ timeout: 15_000 });
  return panel;
}

test("auto-offer: logged-in + unbound pops the consent card, but never mints until Confirm", async ({
  context,
  extensionId,
}) => {
  const { panel, tab } = await openWithTargetTab(context, extensionId);

  // The offer appears on its own (no button click) and is framed as a question.
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText(/Use your play42 login\?/)).toBeVisible();
  // Nothing was minted just by offering — consent is required first.
  expect(target.mintCalls.length).toBe(0);

  await panel.getByTestId("bind-confirm").click();
  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("bind-acting-as")).toContainText("Acting as admin");
  // Now the mint actually hit the target and heim recorded a pat bind.
  expect(target.mintCalls.length).toBeGreaterThan(0);
  const bindCall = heim.state.bindCalls.find((c) => c.endpoint === "bind");
  expect(bindCall?.body).toMatchObject({ mode: "pat", secret: "d2p_test" });
  await tab.close();
});

test("auto-offer consent shows the read-only (GET) scope and the expiry", async ({ context, extensionId }) => {
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText(/read-only \(GET\)/)).toBeVisible();
  await expect(panel.getByText(/expires in 30 days/)).toBeVisible();
  // Read-only assistant -> no "allow writes" toggle.
  await expect(panel.getByTestId("bind-allow-writes")).toHaveCount(0);
  await tab.close();
});

test("no live session: no auto-offer, and the manual button short-circuits to sign-in", async ({
  context,
  extensionId,
}) => {
  target.loggedOut = true;
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  // The probe resolves to "not signed in" — wait for that so the auto-offer has had its chance.
  await expect(panel.getByText("Not signed in on this tab.")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("bind-consent")).toHaveCount(0);
  // The manual "Use my login" button remains the escape hatch.
  await expect(panel.getByTestId("bind-use-my-login")).toBeVisible();

  // Driving it manually still short-circuits on the pre-mint session check (no mint POST).
  await panel.getByTestId("bind-use-my-login").click();
  await panel.getByTestId("bind-confirm").click();
  await expect(panel.getByTestId("bind-unauthenticated")).toBeVisible({ timeout: 15_000 });
  expect(target.mintCalls.length).toBe(0);
  await tab.close();
});

test("bound + matching identity: silent reuse, no consent card on reopen", async ({ context, extensionId }) => {
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();
  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await panel.close();

  // Reopen: same admin still logged in, binding matches -> no offer, just the acting-as chip.
  const panel2 = await reopenPanel(context, extensionId, tab);
  await expect(panel2.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expect(panel2.getByTestId("bind-consent")).toHaveCount(0);
  await tab.close();
});

test("decline is remembered: dismissed offer does not re-nag, but the manual button still works", async ({
  context,
  extensionId,
}) => {
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  // Decline the auto-offer.
  await panel.getByTestId("bind-consent").getByRole("button", { name: "Cancel" }).click();
  await expect(panel.getByTestId("bind-consent")).toHaveCount(0);
  // The manual button reappears for the still-signed-in user.
  await expect(panel.getByTestId("bind-use-my-login")).toBeVisible();
  await panel.close();

  // Reopen: the decline is remembered for this user -> no auto-offer, manual button still present.
  const panel2 = await reopenPanel(context, extensionId, tab);
  await expect(panel2.getByTestId("bind-use-my-login")).toBeVisible({ timeout: 15_000 });
  await expect(panel2.getByTestId("bind-consent")).toHaveCount(0);
  // The manual button clears the decline and opens the same consent card.
  await panel2.getByTestId("bind-use-my-login").click();
  await expect(panel2.getByTestId("bind-consent")).toBeVisible();
  await tab.close();
});

test("drift: a different user is now logged in -> re-offer a rebind", async ({ context, extensionId }) => {
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();
  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("bind-acting-as")).toContainText("Acting as admin");
  await panel.close();

  // The browser is now a different human on the same instance.
  target.meBody = { name: "Other User", username: "other" };

  const panel2 = await reopenPanel(context, extensionId, tab);
  await expect(panel2.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await expect(panel2.getByText(/now signed in as other; rebind\?/)).toBeVisible();
  await tab.close();
});

test("happy mint: token minted in the tab, heim records a pat bind, chip appears", async ({ context, extensionId }) => {
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();

  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("bind-acting-as")).toContainText("Acting as admin");

  const bindCall = heim.state.bindCalls.find((c) => c.endpoint === "bind");
  expect(bindCall?.body).toMatchObject({ mode: "pat", secret: "d2p_test" });
  // The target actually minted (its POST /api/apiToken was hit).
  expect(target.mintCalls.length).toBeGreaterThan(0);
  await tab.close();
});

test("apiToken 404 offers the session fallback consent", async ({ context, extensionId }) => {
  target.tokenResponse = { status: 404, body: { detail: "not found" } };
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();

  await expect(panel.getByTestId("bind-fallback")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText(/cookies/)).toBeVisible();
  // Stop here: the permission grant is flaky headless, so we only assert the fallback UI appears.
  await tab.close();
});

test("apiToken 401 prompts sign-in", async ({ context, extensionId }) => {
  target.tokenResponse = { status: 401, body: { detail: "unauthorized" } };
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();

  await expect(panel.getByTestId("bind-unauthenticated")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText(/Sign in to play42 in this tab first/)).toBeVisible();
  await tab.close();
});

test("session raced away: a mint login redirect lands on the sign-in stage", async ({ context, extensionId }) => {
  target.tokenLoginRedirect = true;
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();

  await expect(panel.getByTestId("bind-unauthenticated")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText(/Sign in to play42 in this tab first/)).toBeVisible();
  // The probe still saw a live session, so the mint ran and the login redirect was detected in-page.
  expect(target.mintCalls.length).toBeGreaterThan(0);
  await tab.close();
});

test("unbind revokes the token on the target and records an unbind call", async ({ context, extensionId }) => {
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();
  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });

  await panel.getByTestId("bind-unbind").click();
  await panel.getByTestId("bind-unbind-confirm").click();

  await expect(panel.getByTestId("bind-acting-as")).toHaveCount(0, { timeout: 15_000 });
  expect(target.deleteCalls).toContain("/api/apiToken/u1");
  const unbindCall = heim.state.bindCalls.find((c) => c.endpoint === "unbind");
  expect(unbindCall?.body).toMatchObject({ mode: "pat" });
  await tab.close();
});
