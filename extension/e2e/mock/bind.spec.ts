// "Use my login" bind flow: the panel mints a scoped credential in the target site's own context
// (its cookies) and hands kodo only the secret. Drives the flow against a KodoMock (the bind
// endpoint) plus a TargetSiteMock (a stand-in DHIS2 the content tab opens): consent copy, a happy
// PAT mint, the 404 -> session-fallback and 401 -> sign-in branches, and unbind (revoke + call).

import { test, expect, openPanel, seedSettings } from "../fixtures";
import { KodoMock, TargetSiteMock, bindAssistant } from "../mockServer";
import type { BrowserContext, Page } from "@playwright/test";

const kodo = new KodoMock();
const target = new TargetSiteMock();

test.beforeAll(async () => {
  await kodo.start();
  await target.start();
});
test.afterAll(async () => {
  await kodo.stop();
  await target.stop();
});
test.beforeEach(() => {
  kodo.reset();
  kodo.state.phase = "ready";
  target.reset();
  kodo.state.assistant = bindAssistant(target.baseUrl());
});

/** Open the panel (talking to kodo), then a content tab on the target site so the tab matches. */
async function openWithTargetTab(context: BrowserContext, extensionId: string): Promise<{ panel: Page; tab: Page }> {
  await seedSettings(context, extensionId, { baseUrl: kodo.baseUrl(), token: "" });
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

test("happy mint: token minted in the tab, kodo records a pat bind, chip appears", async ({ context, extensionId }) => {
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await panel.getByTestId("bind-use-my-login").click();
  await panel.getByTestId("bind-confirm").click();

  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("bind-acting-as")).toContainText("Acting as admin");

  const bindCall = kodo.state.bindCalls.find((c) => c.endpoint === "bind");
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

test("unbind revokes the token on the target and records an unbind call", async ({ context, extensionId }) => {
  const { panel, tab } = await openWithTargetTab(context, extensionId);
  await panel.getByTestId("bind-use-my-login").click();
  await panel.getByTestId("bind-confirm").click();
  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });

  await panel.getByTestId("bind-unbind").click();
  await panel.getByTestId("bind-unbind-confirm").click();

  await expect(panel.getByTestId("bind-acting-as")).toHaveCount(0, { timeout: 15_000 });
  expect(target.deleteCalls).toContain("/api/apiToken/u1");
  const unbindCall = kodo.state.bindCalls.find((c) => c.endpoint === "unbind");
  expect(unbindCall?.body).toMatchObject({ mode: "pat" });
  await tab.close();
});
