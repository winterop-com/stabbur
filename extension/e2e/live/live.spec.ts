// Live E2E: the extension panel driving a real `kodo serve` (locked gemma model +
// DHIS2 CLI bridge against the public play demo). Serial, single flow. Skips
// cleanly if the demo instance is unreachable.
//
// Two serial tests share ONE warm server (started inside test 1, stopped in afterAll)
// so the second test doesn't pay the cold gemma load again:
//   1. connect, cold-load, tool-using chat, verify, tab-mismatch.
//   2. "Use my login" bind: log into play42 in a tab, mint a read-only PAT in that
//      tab's own context, install it via /api/assistant/bind, chat as the bound
//      account, verify over the PAT profile, then unbind (revoke + profile removal).
//
// Long waits are explicit expect timeouts, never Playwright defaults: the cold
// model load is the long pole (budget 600s) and the tool-using answer up to 300s.

import { readFileSync } from "node:fs";
import path from "node:path";
import type { Page } from "@playwright/test";
import { test, expect, openPanel, seedSettings, grantHostPermission } from "../fixtures";
import {
  countLlamaServers,
  LIVE_MODEL,
  LIVE_PORT,
  PLAY_BASE_URL,
  preflight,
  startLiveServer,
  warmBridge,
  type LiveServer,
} from "./liveServer";

const BASE_URL = `http://127.0.0.1:${LIVE_PORT}`;

/** Is the panel page still responsive? (An abandoned permission prompt can wedge its renderer.) */
async function panelResponsive(panel: Page): Promise<boolean> {
  return Promise.race([
    panel
      .evaluate(() => true)
      .then(() => true)
      .catch(() => false),
    new Promise<boolean>((r) => setTimeout(() => r(false), 3000)),
  ]);
}

let skipReason: string | null = null;
let server: LiveServer | null = null;
let baselineLlama = 0;

test.describe.serial("live extension against real kodo + DHIS2", () => {
  test.beforeAll(async () => {
    skipReason = await preflight(PLAY_BASE_URL);
    if (skipReason) return;
    baselineLlama = countLlamaServers();
    warmBridge(); // best-effort cache warm
  });

  test.afterAll(async () => {
    if (server) await server.stop();
    server = null;
    // Orphan check: no NEW stray llama-server after teardown (vs. the baseline of
    // any unrelated ones already running before this run).
    if (skipReason === null) {
      const orphans = countLlamaServers();
      expect(orphans, "no orphan llama-server processes should remain after teardown").toBeLessThanOrEqual(
        baselineLlama,
      );
    }
  });

  test("connect, load, tool-using chat, verify, and tab match", async ({ context, extensionId }) => {
    test.skip(skipReason !== null, skipReason ?? "");

    try {
      // Seed the panel and open it BEFORE the server is up -> disconnected state.
      await seedSettings(context, extensionId, { baseUrl: BASE_URL, token: "" });
      const panel = await openPanel(context, extensionId);
      await expect(panel.getByText(/kodo is not reachable/)).toBeVisible({ timeout: 20_000 });

      // Now boot kodo, allowing this extension's origin through the cross-site guard.
      // Kept warm in the module-scoped `server` so test 2 reuses the loaded model.
      server = startLiveServer(extensionId);

      // Poll until the panel reaches ready. The panel auto-retries (3s) and then
      // polls the loading model (cold gemma load can take minutes; budget 600s).
      const composer = panel.getByPlaceholder(/Message \(Enter to send/);
      await expect(composer).toBeVisible({ timeout: 600_000 });

      // Locked model name surfaced via Settings -> Test connection (the panel's
      // ready view doesn't otherwise print the model name).
      await panel.getByRole("button", { name: "Settings" }).click();
      await panel.getByRole("button", { name: "Test connection" }).click();
      await expect(panel.getByText(new RegExp(`model: ${LIVE_MODEL.replace(/[/-]/g, "\\$&")}`))).toBeVisible({
        timeout: 30_000,
      });
      // exact:true — the backend switcher's "Add backend" / "Remove backend" buttons also
      // contain the substring "Back", which a loose name match would collide with.
      await panel.getByRole("button", { name: "Back", exact: true }).click();
      await expect(composer).toBeVisible();

      // The assistant target banner shows the play42 metadata.
      await expect(panel.getByText("play42", { exact: true })).toBeVisible({ timeout: 30_000 });

      // Ask a question that requires the DHIS2 tools; expect a call + result chip
      // and a final assistant message containing a number.
      await composer.fill("How many organisation units are there? Use the dhis2 tools.");
      await panel.getByRole("button", { name: "Send" }).click();

      await expect(panel.getByTestId("tool-chip-call").first()).toBeVisible({ timeout: 300_000 });
      await expect(panel.getByTestId("tool-chip-result").first()).toBeVisible({ timeout: 300_000 });

      // Wait for the final ASSISTANT bubble to contain a number, and capture it.
      // Assistant content renders in `div.break-words` (markdown); the user bubble
      // is `div.whitespace-pre-wrap.break-words`, so exclude that to avoid echoing
      // the question back.
      const assistantText = panel.locator("div.break-words:not(.whitespace-pre-wrap)");
      let answer = "";
      await expect
        .poll(
          async () => {
            const texts = await assistantText.allInnerTexts();
            answer = texts.length ? texts[texts.length - 1] : "";
            return /\d/.test(answer);
          },
          { timeout: 300_000, intervals: [2000] },
        )
        .toBe(true);
      const callDetail = (await panel.getByTestId("tool-chip-call").first().innerText()).replace(/\s+/g, " ").trim();
      console.log(`[live] tool call: ${callDetail}`);
      console.log(`[live] org-units answer: ${answer.replace(/\s+/g, " ").trim()}`);
      expect(answer).toMatch(/\d/);

      // Verify the target instance (runs the profile-verify tool server-side).
      await panel.getByRole("button", { name: "Verify" }).click();
      await expect(panel.getByText("Verified.")).toBeVisible({ timeout: 60_000 });

      // A tab under a different origin than the assistant target -> mismatch banner.
      const tab = await context.newPage();
      await tab.goto("https://example.com/", { timeout: 30_000 });
      await tab.bringToFront();
      await expect(panel.getByText("This tab does not match the assistant target.")).toBeVisible({
        timeout: 30_000,
      });
      await tab.close();
      await panel.close();
    } catch (err) {
      if (server) console.log(`[live] kodo serve log tail:\n${server.tailLog(60)}`);
      throw err;
    }
  });

  test("binds to the browser login (Use my login: mint a read-only PAT as the browser user)", async ({
    context,
    extensionId,
  }) => {
    test.skip(skipReason !== null, skipReason ?? "");
    test.skip(server === null, "live server not started (test 1 failed)");
    const srv = server!;
    const profilesPath = path.join(srv.dir, ".dhis2", "profiles.toml");

    let tab: Page | null = null;
    let panel: Page | null = null;
    try {
      // Fresh panel against the already-warm server (no second cold load).
      await seedSettings(context, extensionId, { baseUrl: BASE_URL, token: "" });
      panel = await openPanel(context, extensionId);
      const composer = panel.getByPlaceholder(/Message \(Enter to send/);
      await expect(composer).toBeVisible({ timeout: 180_000 });

      // Try to give the extension host access to the play origin NOW, while the panel is the focused
      // page (no other tab open yet — the standalone-probe condition). This is the runtime activeTab
      // stand-in (see grantHostPermission in fixtures) that lets the in-tab mint/probe executeScript
      // run. It
      // may not be grantable in headless (the prompt wedges); if so we still assert everything that
      // does NOT need host access and skip only the mint-dependent tail.
      const hostGranted = await grantHostPermission(panel, new URL(PLAY_BASE_URL).origin);

      // (a) Open a tab on the play instance and log in as admin/district. The form login
      // app varies across builds, so we authenticate via the JSON /api/auth/login endpoint
      // (2.41+) directly in the page context — it sets the JSESSIONID cookie the mint needs —
      // and assert the session by reading /api/me.json in-page.
      tab = await context.newPage();
      await tab.goto(`${PLAY_BASE_URL}/`, { timeout: 60_000, waitUntil: "domcontentloaded" }).catch(() => {});
      const login = await tab.evaluate(async (base: string) => {
        const r = await fetch(`${base}/api/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ username: "admin", password: "district" }),
        });
        const me = await fetch(`${base}/api/me.json?fields=name,username`, { headers: { Accept: "application/json" } });
        const body = me.ok ? ((await me.json()) as { username?: string; name?: string }) : null;
        return { loginStatus: r.status, meOk: me.ok, username: body?.username, name: body?.name };
      }, PLAY_BASE_URL);
      console.log(`[live-bind] login: ${JSON.stringify(login)}`);
      expect(login.meOk, `play login failed: ${JSON.stringify(login)}`).toBe(true);
      expect(login.username).toBe("admin");

      // Land the tab on a real signed-in page under the base path so tab-match succeeds, then
      // focus it: "Use my login" only appears when the active tab matches the assistant target.
      await tab
        .goto(`${PLAY_BASE_URL}/dhis-web-dashboard/`, { timeout: 60_000, waitUntil: "domcontentloaded" })
        .catch(() => {});
      await tab.bringToFront();

      // If the grant was abandoned it may have wedged the panel renderer; only drive it if alive.
      if (hostGranted || (await panelResponsive(panel))) {
        // (b) The panel tracks the active tab and offers the bind (live tab-match against play42).
        await expect(panel.getByTestId("bind-use-my-login")).toBeVisible({ timeout: 60_000 });
        console.log("[live-bind] bind-use-my-login visible");

        // (c) Consent card: read-only (GET) scope, 30-day expiry, profile storage note, and
        // NO "allow writes" toggle (the dhis2 template is read-only). Needs no host access.
        await panel.getByTestId("bind-use-my-login").click({ timeout: 15_000 });
        await expect(panel.getByTestId("bind-consent")).toBeVisible({ timeout: 15_000 });
        await expect(panel.getByText(/read-only \(GET\)/)).toBeVisible({ timeout: 15_000 });
        await expect(panel.getByText(/expires in 30 days/)).toBeVisible({ timeout: 15_000 });
        await expect(panel.getByText(/stored in the kodo project.s DHIS2 profile/)).toBeVisible({ timeout: 15_000 });
        await expect(panel.getByTestId("bind-allow-writes")).toHaveCount(0);
        console.log("[live-bind] consent card asserted (read-only GET, 30-day, profile-stored, no writes)");
      }

      // The in-tab PAT mint needs host access to the play origin. When headless can't grant it, skip
      // the mint-dependent tail — the full mint -> install -> chat -> verify -> unbind is verified
      // out-of-band against real play42 (see WORKLOG) and its UI is covered by e2e/mock/bind.spec.ts.
      if (!hostGranted) {
        test.info().annotations.push({
          type: "environment-limitation",
          description:
            "Headless Chromium cannot grant the optional host permission for the play origin " +
            "(chrome.permissions.request's prompt wedges the renderer), which the in-tab PAT mint " +
            "requires. Live-asserted here: real play login, tab-match, and the read-only consent card. " +
            "The full mint->install->chat->verify->unbind was verified out-of-band against real play42 " +
            "(WORKLOG) and via e2e/mock/bind.spec.ts.",
        });
        console.log("[live-bind] host grant unavailable in headless — mint tail skipped (verified out-of-band)");
        return;
      }

      console.log("[live-bind] clicking Create token");
      await panel.getByTestId("bind-confirm").click({ timeout: 15_000 });

      // (d) The token is minted in the tab and installed; the banner shows the bound account.
      // If the mint failed the panel shows bind-error/fallback/unauthenticated instead — surface it.
      const outcome = panel.getByTestId("bind-acting-as").or(panel.getByTestId("bind-error"))
        .or(panel.getByTestId("bind-fallback")).or(panel.getByTestId("bind-unauthenticated"));
      await expect(outcome.first()).toBeVisible({ timeout: 120_000 });
      const stage = (await panel.getByTestId("bind-flow").innerText().catch(() => "")).replace(/\s+/g, " ").trim();
      console.log(`[live-bind] post-confirm stage: ${stage || "(bound; bind-flow closed)"}`);
      await expect(panel.getByTestId("bind-acting-as")).toBeVisible({ timeout: 90_000 });
      await expect(panel.getByTestId("bind-acting-as")).toContainText("admin");
      // kodo ran `d2w profile add play42 --auth pat --local` in the fixture dir: the profile
      // flipped from basic to a PAT profile.
      const boundProfiles = readFileSync(profilesPath, "utf8");
      console.log(`[live-bind] profiles.toml after bind:\n${boundProfiles}`);
      expect(boundProfiles).toContain('auth = "pat"');

      // (e) Chat as the bound account. The PAT acts as admin (we logged in as admin), so the
      // browser user and the tool account CONVERGE — the answer should reference admin.
      await composer.fill("Who am I? Check with the dhis2 tools which account the tools use.");
      await panel.getByRole("button", { name: "Send" }).click({ timeout: 15_000 });
      await expect(panel.getByTestId("tool-chip-call").first()).toBeVisible({ timeout: 300_000 });
      const assistantText = panel.locator("div.break-words:not(.whitespace-pre-wrap)");
      let answer = "";
      await expect
        .poll(
          async () => {
            const texts = await assistantText.allInnerTexts();
            answer = texts.length ? texts[texts.length - 1] : "";
            return /admin/i.test(answer);
          },
          { timeout: 300_000, intervals: [2000] },
        )
        .toBe(true);
      console.log(`[live-bind] who-am-I answer: ${answer.replace(/\s+/g, " ").trim()}`);
      expect(answer).toMatch(/admin/i);

      // (f) Verify now runs `d2w profile verify play42` over the freshly-bound PAT profile.
      await panel.getByRole("button", { name: "Verify" }).click({ timeout: 15_000 });
      await expect(panel.getByText("Verified.")).toBeVisible({ timeout: 60_000 });

      // (h) GET-only proof: the raw token never leaves the browser (kodo only stores it in the
      // profile), so we can't replay it here. Instead we assert the consent said read-only (GET)
      // (above) and that the target banner shows the read-only chip — the strongest observable
      // signal that the bound credential is scoped to reads.
      await expect(panel.getByText("read-only", { exact: true })).toBeVisible();

      // (g) Unbind: revoke the token in the tab, remove the profile, and the "Use my login"
      // affordance returns.
      await panel.getByTestId("bind-unbind").click({ timeout: 15_000 });
      await panel.getByTestId("bind-unbind-confirm").click({ timeout: 15_000 });
      await expect(panel.getByTestId("bind-use-my-login")).toBeVisible({ timeout: 60_000 });
      const unboundProfiles = readFileSync(profilesPath, "utf8");
      console.log(`[live-bind] profiles.toml after unbind:\n${unboundProfiles}`);
      expect(unboundProfiles).not.toContain('auth = "pat"');

      // Best-effort revoke check: the panel DELETEs /api/apiToken/<uid> in the tab during unbind.
      // Confirm no kodo-created token remains — do NOT fail the suite on it (revoke is best-effort).
      const remaining = await tab.evaluate(async (base: string) => {
        const url = `${base}/api/apiToken.json?fields=uid,description&filter=description:eq:${encodeURIComponent(
          "kodo assistant token",
        )}`;
        const r = await fetch(url, { headers: { Accept: "application/json" } });
        if (!r.ok) return -1;
        const b = (await r.json()) as { pager?: { total?: number }; apiToken?: unknown[] };
        return b.pager?.total ?? b.apiToken?.length ?? -1;
      }, PLAY_BASE_URL);
      console.log(`[live-bind] kodo tokens remaining on play42 after unbind: ${remaining}`);
      if (typeof remaining === "number" && remaining > 0) {
        console.warn(`[live-bind] revoke best-effort: ${remaining} kodo token(s) still present on play42`);
      }
      // NOTE: bind REPLACED the demo basic profile, and unbind REMOVED it — the fixture now has no
      // play42 profile. This is the last test and afterAll drops the whole fixture dir, so no
      // restore is needed; if a later test is ever added, restore .dhis2/profiles.toml first.
    } finally {
      if (server) console.log(`[live-bind] kodo serve log tail:\n${server.tailLog(40)}`);
      if (tab) await tab.close().catch(() => {});
      if (panel) await panel.close().catch(() => {});
    }
  });
});
