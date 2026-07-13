// Write-scoped "Use my login" binds. A writable assistant surfaces an "allow writes" toggle;
// enabling it records a write-scoped binding (PAT minted with the full method set) that
// TargetBanner reflects as "writes enabled". A session-mode write bind additionally captures the
// XSRF token and ships it to heim as `extra_secret`.

import { test, expect, openPanel, seedSettings, expandTarget } from "../fixtures";
import { TAB_MATCHED } from "../../lib/bannerText";
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
});

/** A writable (readonly:false) DHIS2-style assistant pointed at the target site. */
function writableAssistant(baseUrl: string): Record<string, unknown> {
  return { ...bindAssistant(baseUrl), readonly: false };
}

/** A writable, session-only assistant (no PAT mint recipe -> flow opens at the session fallback). */
function sessionOnlyAssistant(baseUrl: string): Record<string, unknown> {
  const a = writableAssistant(baseUrl);
  const bind = { ...(a.bind as Record<string, unknown>) };
  delete bind.mint_mode;
  delete bind.mint_path;
  delete bind.mint_payload;
  bind.modes = ["session"];
  a.bind = bind;
  return a;
}

async function openWithTargetTab(context: BrowserContext, extensionId: string): Promise<{ panel: Page; tab: Page }> {
  await seedSettings(context, extensionId, { baseUrl: heim.baseUrl(), token: "" });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  const tab = await context.newPage();
  await tab.goto(`${target.baseUrl()}/dhis`);
  await tab.bringToFront();
  // The login-binding section renders once the panel sees the tab matching the target. For a
  // logged-in matched tab the consent (or session fallback) card then auto-offers on its own.
  await expect(panel.getByText(TAB_MATCHED)).toBeVisible({ timeout: 15_000 });
  return { panel, tab };
}

/** Best-effort grant of the extension's optional `cookies` permission (already-held host origin +
 *  cookies). Returns false if the (flaky, headless) permission prompt does not resolve. */
async function grantCookies(panel: Page, origin: string): Promise<boolean> {
  const pattern = `${origin}/*`;
  if (await panel.evaluate((p) => chrome.permissions.contains({ permissions: ["cookies"], origins: [p] }), pattern))
    return true;
  await panel.evaluate((p) => {
    const b = document.createElement("button");
    b.id = "__grant_cookies";
    b.textContent = "grant cookies"; // needs text + size or Playwright's click never finds it actionable
    b.style.cssText = "position:fixed;bottom:0;left:0;z-index:9999;width:140px;height:32px";
    b.addEventListener("click", () => {
      void chrome.permissions.request({ permissions: ["cookies"], origins: [p] }).then((g) => {
        (window as unknown as { __cookiesGranted?: boolean }).__cookiesGranted = g;
      });
    });
    document.body.appendChild(b);
  }, pattern);
  await panel
    .locator("#__grant_cookies")
    .click({ timeout: 10_000 })
    .catch(() => {});
  const granted = await Promise.race([
    panel
      .waitForFunction(() => (window as unknown as { __cookiesGranted?: boolean }).__cookiesGranted === true, {
        timeout: 20_000,
      })
      .then(() => true)
      .catch(() => false),
    new Promise<boolean>((r) => setTimeout(() => r(false), 22_000)),
  ]);
  if (granted) await panel.evaluate(() => document.getElementById("__grant_cookies")?.remove()).catch(() => {});
  return granted;
}

test("a PAT write bind records write scope and TargetBanner shows 'writes enabled'", async ({
  context,
  extensionId,
}) => {
  heim.state.assistant = writableAssistant(target.baseUrl());
  const { panel, tab } = await openWithTargetTab(context, extensionId);

  // The consent card auto-offers; a writable assistant offers the write toggle — enable it.
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-allow-writes").check();
  await panel.getByTestId("bind-confirm").click();

  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expandTarget(panel); // bind-scope lives in the expanded binding controls
  await expect(panel.getByTestId("bind-scope")).toContainText("writes enabled");

  // The full method set reached the target's mint endpoint (write scope was requested).
  expect(target.mintCalls.length).toBeGreaterThan(0);
  expect(target.mintCalls[0]).toContain("DELETE");
  await tab.close();
});

test("a read-only PAT bind shows read-only scope, no writes", async ({ context, extensionId }) => {
  heim.state.assistant = writableAssistant(target.baseUrl());
  const { panel, tab } = await openWithTargetTab(context, extensionId);

  // The consent card auto-offers; leave "allow writes" unchecked -> read-only mint.
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();

  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expandTarget(panel);
  await expect(panel.getByTestId("bind-scope")).toContainText("read-only");
  expect(target.mintCalls[0]).not.toContain("DELETE");
  await tab.close();
});

test("a session write bind captures the XSRF token and sends it as extra_secret", async ({ context, extensionId }) => {
  heim.state.assistant = sessionOnlyAssistant(target.baseUrl());
  // Seed the live session + CSRF cookies on the target origin so the in-panel capture reads them.
  await context.addCookies([
    { url: target.baseUrl(), name: "JSESSIONID", value: "sess-abc" },
    { url: target.baseUrl(), name: "XSRF-TOKEN", value: "xsrf-xyz" },
  ]);

  const { panel, tab } = await openWithTargetTab(context, extensionId);

  // Pre-grant the cookies permission so confirmFallback's own request is a no-op (no headless
  // prompt to wedge). If the grant is unavailable in this environment, skip the assertion tail.
  const origin = new URL(target.baseUrl()).origin;
  const granted = await grantCookies(panel, origin);
  test.skip(!granted, "cookies permission grant unavailable headless");

  // Session-only recipe (no mint) auto-offers straight at the fallback consent, which also offers
  // the write toggle.
  await expect(panel.getByTestId("bind-fallback")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-allow-writes").check();
  await panel.getByTestId("bind-fallback-confirm").click();

  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expandTarget(panel);
  await expect(panel.getByTestId("bind-scope")).toContainText("writes enabled");

  const bindCall = heim.state.bindCalls.find((c) => c.endpoint === "bind");
  expect(bindCall?.body).toMatchObject({ mode: "session", extra_secret: "xsrf-xyz" });
  expect(String(bindCall?.body.secret)).toContain("JSESSIONID=sess-abc");
  await tab.close();
});

// --- Explicit write-scope re-mint (a PAT method scope is fixed at mint, so a cached read-only token
// cannot escalate; the upgrade re-mints with the full method set behind the existing consent). ---

test("write-scope re-mint: a read-only PAT binding on a write assistant upgrades to writes", async ({
  context,
  extensionId,
}) => {
  heim.state.assistant = writableAssistant(target.baseUrl());
  const { panel, tab } = await openWithTargetTab(context, extensionId);

  // Auto-offered consent, confirmed read-only (writes left unchecked) -> a read-only PAT binding.
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();
  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expandTarget(panel); // scope + upgrade affordance live in the expanded binding controls
  await expect(panel.getByTestId("bind-scope")).toContainText("read-only");
  const mintsBeforeUpgrade = target.mintCalls.length;

  // The upgrade affordance appears next to the scope chip; open it -> writes pre-checked, re-mint
  // framing that names the token replacement.
  await expect(panel.getByTestId("bind-upgrade-writes")).toBeVisible();
  await panel.getByTestId("bind-upgrade-writes").click();
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("bind-allow-writes")).toBeChecked();
  await expect(panel.getByTestId("bind-remint-notice")).toBeVisible();
  await panel.getByTestId("bind-confirm").click();

  // Scope flips to writes; the re-mint carried the full method set; the old read-only token was
  // revoked (DELETE on the target); the affordance is gone (no downgrade offered).
  await expect(panel.getByTestId("bind-scope")).toContainText("writes enabled", { timeout: 15_000 });
  expect(target.mintCalls.length).toBeGreaterThan(mintsBeforeUpgrade);
  expect(target.mintCalls[target.mintCalls.length - 1]).toContain("DELETE");
  expect(target.deleteCalls).toContain("/api/apiToken/u1");
  await expect(panel.getByTestId("bind-upgrade-writes")).toHaveCount(0);
  await tab.close();
});

test("write-scope re-mint: switching an open Rebind card to Enable writes re-mints the FULL method set", async ({
  context,
  extensionId,
}) => {
  heim.state.assistant = writableAssistant(target.baseUrl());
  const { panel, tab } = await openWithTargetTab(context, extensionId);

  // Land a read-only PAT binding first (auto-offered consent, writes left unchecked).
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();
  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expandTarget(panel);
  await expect(panel.getByTestId("bind-scope")).toContainText("read-only", { timeout: 15_000 });
  const mintsBefore = target.mintCalls.length;

  // Open the manual Rebind card (source=manual, writes unchecked), THEN escalate to Enable writes
  // while it is open (source=upgrade). Before the key-remount fix, switching source on the mounted
  // flow left allowWrites stale-false -> a read-only re-mint that still revoked the old token. The
  // key remounts the flow so the upgrade's pre-checked writes take, and the full method set is sent.
  await panel.getByRole("button", { name: "Rebind" }).click();
  await expect(panel.getByTestId("bind-consent")).toBeVisible();
  await expect(panel.getByTestId("bind-allow-writes")).not.toBeChecked();
  await panel.getByTestId("bind-upgrade-writes").click();
  await expect(panel.getByTestId("bind-allow-writes")).toBeChecked(); // remounted with writes pre-checked
  await expect(panel.getByTestId("bind-remint-notice")).toBeVisible();
  await panel.getByTestId("bind-confirm").click();

  await expect(panel.getByTestId("bind-scope")).toContainText("writes enabled", { timeout: 15_000 });
  expect(target.mintCalls.length).toBeGreaterThan(mintsBefore);
  expect(target.mintCalls[target.mintCalls.length - 1]).toContain("DELETE");
  await tab.close();
});

test("write-scope re-mint: a writes-scoped binding shows no upgrade affordance", async ({ context, extensionId }) => {
  heim.state.assistant = writableAssistant(target.baseUrl());
  const { panel, tab } = await openWithTargetTab(context, extensionId);

  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-allow-writes").check();
  await panel.getByTestId("bind-confirm").click();

  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expandTarget(panel);
  await expect(panel.getByTestId("bind-scope")).toContainText("writes enabled", { timeout: 15_000 });
  await expect(panel.getByTestId("bind-upgrade-writes")).toHaveCount(0);
  await tab.close();
});

test("write-scope re-mint: a read-only assistant never offers the upgrade", async ({ context, extensionId }) => {
  heim.state.assistant = bindAssistant(target.baseUrl()); // readonly: true
  const { panel, tab } = await openWithTargetTab(context, extensionId);

  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();
  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expandTarget(panel);
  await expect(panel.getByTestId("bind-scope")).toContainText("read-only");
  await expect(panel.getByTestId("bind-upgrade-writes")).toHaveCount(0);
  await tab.close();
});

test("write-scope re-mint: a failed re-mint leaves the read-only binding intact", async ({ context, extensionId }) => {
  heim.state.assistant = writableAssistant(target.baseUrl());
  const { panel, tab } = await openWithTargetTab(context, extensionId);

  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();
  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expandTarget(panel);
  await expect(panel.getByTestId("bind-scope")).toContainText("read-only", { timeout: 15_000 });

  // The re-mint now fails: the session raced away, so the mint POST redirects to the login page.
  target.tokenLoginRedirect = true;
  await panel.getByTestId("bind-upgrade-writes").click();
  await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
  await panel.getByTestId("bind-confirm").click();

  // The mint bounced to sign-in; the old binding is untouched (still read-only) and nothing was
  // revoked (the revoke fires only after a successful bind).
  await expect(panel.getByTestId("bind-unauthenticated")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("bind-scope")).toContainText("read-only");
  expect(target.deleteCalls).toHaveLength(0);
  await tab.close();
});

test("write-scope re-mint: a session-mode binding shows no upgrade affordance", async ({ context, extensionId }) => {
  heim.state.assistant = writableAssistant(target.baseUrl());
  await seedSettings(context, extensionId, { baseUrl: heim.baseUrl(), token: "" });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });

  // Seed a read-only SESSION-mode binding BEFORE the tab matches, so no auto-offer fires and the
  // acting-as chip renders from it. A cookie can't be method-scoped, so the upgrade must never
  // appear even though writes is false.
  // Composite key `${backendId}:${targetId}`: the compat single-assistant target's id is the slugified
  // name ("play42"), so the panel's per-target watcher/reader sees this seeded binding.
  await panel.evaluate(
    (baseUrl) =>
      chrome.storage.local.set({
        "heim-ext-binding:default:play42": {
          backendId: "default",
          targetId: "play42",
          targetBaseUrl: baseUrl,
          mode: "session",
          username: "admin",
          name: "Admin User",
          cookieName: "JSESSIONID",
          writes: false,
        },
      }),
    target.baseUrl(),
  );

  const tab = await context.newPage();
  await tab.goto(`${target.baseUrl()}/dhis`);
  await tab.bringToFront();
  await expect(panel.getByText(TAB_MATCHED)).toBeVisible({ timeout: 15_000 });

  await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 15_000 });
  await expandTarget(panel);
  await expect(panel.getByTestId("bind-scope")).toContainText("read-only");
  await expect(panel.getByTestId("bind-upgrade-writes")).toHaveCount(0);
  await tab.close();
});
