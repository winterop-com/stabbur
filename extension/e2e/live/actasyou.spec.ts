// Live E2E: the act-as-you journey against real heim + the public play demo.
// Complements live.spec.ts (which covers connect/chat/verify and the MANUAL bind):
// this file drives the round-4 default path — sign-in-first, the AUTO-OFFER on a
// matched logged-in tab, decline memory, bind, silent reuse, unbind — plus the
// no-host-access degradation with the GENERIC build (the real-runtime case where
// activeTab is not in effect; regression for the "injection failed" bug).
//
// Serial; both tests share ONE warm server (started in test 1, stopped in afterAll).
// Set HEIM_DRIVE_SHOTS=<dir> to save a screenshot at each step.

import { readFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, type BrowserContext, type Page } from "@playwright/test";
import {
  test,
  expect,
  openPanel,
  seedSettings,
  grantHostPermission,
  resolveExtensionId,
  userDataDir,
  expandTarget,
} from "../fixtures";
import { TAB_MATCHED } from "../../lib/bannerText";
import { LIVE_PORT, PLAY_BASE_URL, preflight, startLiveServer, warmBridge, type LiveServer } from "./liveServer";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE_URL = `http://127.0.0.1:${LIVE_PORT}`;
const GENERIC_EXTENSION = path.resolve(HERE, "..", "..", ".output", "chrome-mv3");
const MATCH_TEXT = TAB_MATCHED;
const SHOTS = process.env.HEIM_DRIVE_SHOTS ?? "";
const HEADED = process.env.HEADED === "1" || process.env.HEADED === "true";

let shotIndex = 0;
async function shot(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  shotIndex += 1;
  await page.screenshot({ path: path.join(SHOTS, `${String(shotIndex).padStart(2, "0")}-${name}.png`) });
}

/** Log into play as admin/district via the JSON auth endpoint in the tab's own context. */
async function loginAsAdmin(tab: Page): Promise<void> {
  await tab.goto(`${PLAY_BASE_URL}/`, { timeout: 60_000, waitUntil: "domcontentloaded" }).catch(() => {});
  const login = await tab.evaluate(async (base: string) => {
    const r = await fetch(`${base}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ username: "admin", password: "district" }),
    });
    const me = await fetch(`${base}/api/me.json?fields=username`, { headers: { Accept: "application/json" } });
    const body = me.ok ? ((await me.json()) as { username?: string }) : null;
    return { loginStatus: r.status, meOk: me.ok, username: body?.username };
  }, PLAY_BASE_URL);
  expect(login.meOk, `play login failed: ${JSON.stringify(login)}`).toBe(true);
  expect(login.username).toBe("admin");
  await tab
    .goto(`${PLAY_BASE_URL}/dhis-web-dashboard/`, { timeout: 60_000, waitUntil: "domcontentloaded" })
    .catch(() => {});
}

function profilesText(srv: LiveServer): string {
  try {
    return readFileSync(path.join(srv.dir, ".dhis2", "profiles.toml"), "utf8");
  } catch {
    return "";
  }
}

let skipReason: string | null = null;
let server: LiveServer | null = null;

test.describe.serial("act-as-you against real heim + play42", () => {
  test.beforeAll(async () => {
    skipReason = await preflight(PLAY_BASE_URL);
    if (skipReason) return;
    warmBridge();
  });

  test.afterAll(async () => {
    if (server) await server.stop();
    server = null;
  });

  test("sign-in-first, auto-offer, decline memory, bind, silent reuse, unbind", async ({
    context,
    extensionId,
  }) => {
    test.skip(skipReason !== null, skipReason ?? "");
    test.setTimeout(1_500_000); // cold model load alone can take minutes

    try {
      await seedSettings(context, extensionId, { baseUrl: BASE_URL, token: "" });
      let panel = await openPanel(context, extensionId);
      server = startLiveServer(extensionId);
      const composer = panel.getByPlaceholder(/Message \(Enter to send/);
      await expect(composer).toBeVisible({ timeout: 600_000 });

      // The e2e build pre-grants the play origin statically; requestHostAccess resolves silently.
      const granted = await grantHostPermission(panel, new URL(PLAY_BASE_URL).origin);
      expect(granted, "run with the e2e build: `bun run build:e2e` + HEIM_E2E_BUILD=1").toBe(true);

      // (1) Sign-in-first: matched tab, NOT logged in -> no auto-offer; the manual path routes to
      // the sign-in stage instead of a raw status error.
      const tab = await context.newPage();
      await tab.goto(`${PLAY_BASE_URL}/`, { timeout: 60_000, waitUntil: "domcontentloaded" }).catch(() => {});
      await tab.bringToFront();
      await expect(panel.getByText(MATCH_TEXT)).toBeVisible({ timeout: 60_000 });
      await panel.waitForTimeout(5000); // let the auto-probe settle
      await expect(panel.getByTestId("bind-consent")).toHaveCount(0);
      await panel.getByTestId("bind-use-my-login").click({ timeout: 15_000 });
      await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
      await panel.getByTestId("bind-confirm").click({ timeout: 15_000 });
      await expect(panel.getByTestId("bind-unauthenticated")).toBeVisible({ timeout: 60_000 });
      await shot(panel, "signin-first");
      await panel
        .getByTestId("bind-unauthenticated")
        .getByRole("button", { name: /Back|Cancel|Close/ })
        .first()
        .click();

      // (2) Log in as admin -> the AUTO-OFFER appears without any click.
      await loginAsAdmin(tab);
      await tab.bringToFront();
      await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 60_000 });
      await expect(panel.getByText("Use your play42 login?")).toBeVisible({ timeout: 15_000 });
      await shot(panel, "auto-offer");

      // (3) Decline memory: cancel, reopen the panel -> no re-nag, manual button still there.
      await panel.getByTestId("bind-consent").getByRole("button", { name: "Cancel" }).click();
      await expect(panel.getByTestId("bind-consent")).toHaveCount(0);
      await panel.close();
      panel = await openPanel(context, extensionId);
      await tab.bringToFront();
      await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 180_000 });
      await expect(panel.getByText(MATCH_TEXT)).toBeVisible({ timeout: 60_000 });
      await panel.waitForTimeout(8000); // auto-probe + offer window
      await expect(panel.getByTestId("bind-consent")).toHaveCount(0);
      await expect(panel.getByTestId("bind-use-my-login")).toBeVisible();
      await shot(panel, "no-renag-after-decline");

      // (4) Manual bind -> acting-as chip + the project profile flips to PAT auth.
      await panel.getByTestId("bind-use-my-login").click({ timeout: 15_000 });
      await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
      await panel.getByTestId("bind-confirm").click({ timeout: 15_000 });
      await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 120_000 });
      await expect(panel.getByTestId("bind-acting-as")).toContainText("admin");
      await expect
        .poll(() => profilesText(server!).includes('auth = "pat"'), { timeout: 30_000, intervals: [1000] })
        .toBe(true);
      await shot(panel, "bound-acting-as-admin");

      // (5) Silent reuse: reopen -> chip present, no consent card.
      await panel.close();
      panel = await openPanel(context, extensionId);
      await tab.bringToFront();
      await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 180_000 });
      await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 60_000 });
      await panel.waitForTimeout(8000);
      await expect(panel.getByTestId("bind-consent")).toHaveCount(0);
      await shot(panel, "silent-reuse");

      // (6) Verify runs over the bound PAT profile. Verify + Unbind live in the expanded detail now.
      await expandTarget(panel);
      await panel.getByRole("button", { name: "Verify" }).click();
      await expect(panel.getByText("Verified.")).toBeVisible({ timeout: 120_000 });

      // (7) Unbind: token revoked in the tab, profile removed, chip gone, manual button back.
      await expandTarget(panel);
      await panel.getByTestId("bind-unbind").click({ timeout: 15_000 });
      await panel.getByTestId("bind-unbind-confirm").click({ timeout: 15_000 });
      await expect(panel.getByTestId("bind-acting-as")).toHaveCount(0, { timeout: 60_000 });
      await expect(panel.getByTestId("bind-use-my-login")).toBeVisible({ timeout: 60_000 });
      await expect
        .poll(() => !profilesText(server!).includes('auth = "pat"'), { timeout: 30_000, intervals: [1000] })
        .toBe(true);
      await shot(panel, "unbound");

      await tab.close();
      await panel.close();
    } catch (err) {
      if (server) console.log(`[actasyou] heim serve log tail:\n${server.tailLog(60)}`);
      throw err;
    }
  });

  test("generic build without host access degrades to the no-access hint (no injection-failed error)", async () => {
    test.skip(skipReason !== null, skipReason ?? "");
    test.skip(server === null, "live server not started (test 1 failed)");
    test.setTimeout(600_000);

    // A second browser context loading the GENERIC build (no static play grant, and no toolbar-icon
    // activeTab in automation) — exactly the state the manual bug report hit. The auto-probe must
    // surface the no-access hint, not a raw "injection failed" mint error.
    const dir = userDataDir("heim-ext-noaccess-");
    const ctx: BrowserContext = await chromium.launchPersistentContext(dir, {
      channel: "chromium",
      headless: !HEADED,
      args: [
        `--disable-extensions-except=${GENERIC_EXTENSION}`,
        `--load-extension=${GENERIC_EXTENSION}`,
        "--no-first-run",
        "--no-default-browser-check",
      ],
    });
    try {
      const extId = await resolveExtensionId(ctx);
      await seedSettings(ctx, extId, {
        baseUrl: BASE_URL,
        token: "",
        pageContextEnabled: true,
        pageTextEnabled: true,
      });
      const panel = await openPanel(ctx, extId);
      const composer = panel.getByPlaceholder(/Message \(Enter to send/);
      await expect(composer).toBeVisible({ timeout: 180_000 });

      const tab = await ctx.newPage();
      await loginAsAdmin(tab); // fresh cookie jar: log in again in this context
      await tab.bringToFront();
      await expect(panel.getByText(MATCH_TEXT)).toBeVisible({ timeout: 60_000 });
      // The gesture-less auto-probe cannot inject -> the distinct no-access state, silently.
      await expect(panel.getByText(/heim has no access to this site yet/)).toBeVisible({ timeout: 60_000 });
      await expect(panel.getByTestId("bind-consent")).toHaveCount(0);
      await expect(panel.getByText(/injection failed/)).toHaveCount(0);
      await shot(panel, "generic-noaccess-hint");

      // Send with page context on: the capture cannot run (no grant, and nobody answers the
      // permission prompt), so the composer chip must say the page was NOT captured — the old
      // behavior claimed "page context attached" while attaching no page at all.
      await composer.fill("hi");
      await panel.getByRole("button", { name: "Send" }).click();
      await expect(panel.getByTestId("page-context-missing")).toBeVisible({ timeout: 30_000 });
      await shot(panel, "generic-honest-missing-chip");
    } finally {
      await ctx.close().catch(() => {});
    }
  });

  test("the dhis2 assistant answers a general page question from attached page text", async () => {
    test.skip(skipReason !== null, skipReason ?? "");
    test.skip(server === null, "live server not started (test 1 failed)");
    test.setTimeout(600_000);

    // Server-side check of the dhis2 template's system prompt (the panel path is covered above):
    // with real page text attached, "what are the top stories today" must be answered FROM the
    // page, not deflected with "I don't have access to news sites".
    const pageText = [
      "1. Local model beats cloud benchmark (example.com) 312 points",
      "2. New TypeScript compiler released (typescriptlang.org) 245 points",
      "3. Rust in the kernel: a status update (lwn.net) 198 points",
    ].join("\n");
    const content =
      `Page URL: https://news.ycombinator.com/\nPage title: Hacker News\n` +
      `Page text (truncated): ${pageText}\n\nwhat are the top stories today`;
    const res = await fetch(`${BASE_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: [{ role: "user", content }], use_tools: true }),
    });
    expect(res.ok).toBe(true);
    const raw = await res.text();
    const answer = [...raw.matchAll(/^data: (.+)$/gm)]
      .map((m) => JSON.parse(m[1]) as { type: string; text?: string })
      .filter((e) => e.type === "token")
      .map((e) => e.text ?? "")
      .join("");
    console.log(`[actasyou] page-question answer: ${answer.replace(/\s+/g, " ").slice(0, 300)}`);
    expect(answer).toMatch(/typescript|kernel|benchmark/i);
    expect(answer).not.toMatch(/don't have access|do not have access|cannot browse|unable to browse/i);
  });
});
