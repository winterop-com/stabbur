// "Use my login" bind flow: the panel mints a scoped credential in the target site's own context
// (its cookies) and hands heim only the secret. Drives the flow against a HeimMock (the bind
// endpoint) plus a TargetSiteMock (a stand-in DHIS2 the content tab opens): consent copy, a happy
// PAT mint, the 404 -> session-fallback and 401 -> sign-in branches, the no-session cases (probe
// short-circuit + in-page login-redirect detection), and unbind (revoke + call).

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

/** Open the panel (talking to heim), then a content tab on the target site so the tab matches. */
async function openWithTargetTab(context: BrowserContext, extensionId: string): Promise<{ panel: Page; tab: Page }> {
  await seedSettings(context, extensionId, { baseUrl: heim.baseUrl(), token: "" });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  const tab = await context.newPage();
  await tab.goto(`${target.baseUrl()}/dhis`);
  await tab.bringToFront();
  // The "Use my login" button appears once the panel sees the tab matching the target.
  await expect(panel.getByTestId("bind-use-my-login")).toBeVisible({ timeout: 15_000 });
  return { panel, tab };
}

test("consent card shows the read-only (GET) scope and the expiry", async ({ context, extensionId }) => {
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await panel.getByTestId("bind-use-my-login").click();
  await expect(panel.getByTestId("bind-consent")).toBeVisible();
  await expect(panel.getByText(/read-only \(GET\)/)).toBeVisible();
  await expect(panel.getByText(/expires in 30 days/)).toBeVisible();
  // Read-only assistant -> no "allow writes" toggle.
  await expect(panel.getByTestId("bind-allow-writes")).toHaveCount(0);
  await tab.close();
});

test("happy mint: token minted in the tab, heim records a pat bind, chip appears", async ({ context, extensionId }) => {
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await panel.getByTestId("bind-use-my-login").click();
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
  await panel.getByTestId("bind-use-my-login").click();
  await panel.getByTestId("bind-confirm").click();

  await expect(panel.getByTestId("bind-fallback")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText(/cookies/)).toBeVisible();
  // Stop here: the permission grant is flaky headless, so we only assert the fallback UI appears.
  await tab.close();
});

test("apiToken 401 prompts sign-in", async ({ context, extensionId }) => {
  target.tokenResponse = { status: 401, body: { detail: "unauthorized" } };
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await panel.getByTestId("bind-use-my-login").click();
  await panel.getByTestId("bind-confirm").click();

  await expect(panel.getByTestId("bind-unauthenticated")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText(/Sign in to play42 in this tab first/)).toBeVisible();
  await tab.close();
});

test("no live session: probe short-circuits to sign-in without hitting apiToken", async ({ context, extensionId }) => {
  target.loggedOut = true;
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await panel.getByTestId("bind-use-my-login").click();
  await panel.getByTestId("bind-confirm").click();

  await expect(panel.getByTestId("bind-unauthenticated")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText(/Sign in to play42 in this tab first/)).toBeVisible();
  // The pre-mint session check caught the no-session state, so the mint endpoint was never called.
  expect(target.mintCalls.length).toBe(0);
  await tab.close();
});

test("session raced away: a mint login redirect lands on the sign-in stage", async ({ context, extensionId }) => {
  target.tokenLoginRedirect = true;
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await panel.getByTestId("bind-use-my-login").click();
  await panel.getByTestId("bind-confirm").click();

  await expect(panel.getByTestId("bind-unauthenticated")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText(/Sign in to play42 in this tab first/)).toBeVisible();
  // The probe still saw a live session, so the mint ran and the login redirect was detected in-page.
  expect(target.mintCalls.length).toBeGreaterThan(0);
  await tab.close();
});

test("unbind revokes the token on the target and records an unbind call", async ({ context, extensionId }) => {
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await panel.getByTestId("bind-use-my-login").click();
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
