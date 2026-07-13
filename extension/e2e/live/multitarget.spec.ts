// Live E2E: the multi-target registry against real heim + the public play demo, driven through the
// extension side panel. Proves the shipped `dhis2-multi` template's headline behaviors on a real
// browser + real model:
//   - the panel resolves TWO targets (play42 -> /dev-2-42, play41 -> /dev-2-41) from /api/assistants;
//   - a tab under /dev-2-42 selects play42 and NAVIGATING the SAME tab to /dev-2-41 AUTO-SWITCHES the
//     banner to play41 (same origin, told apart by path) — the headline feature;
//   - per-target Verify runs each target's own profile-verify over its own bridge;
//   - a chat turn while on /dev-2-41 carries `target: "play41"` on POST /api/chat (target-scoped
//     routing) and drives the play41-namespaced tool;
//   - a non-matching tab collapses to the primary-name one-liner "Not a play42 page.".
//
// Serial; ONE warm server (started in the single test, stopped in afterAll). Set HEIM_DRIVE_SHOTS=<dir>
// to save a screenshot at each step.
//
// Degradation: both instances are preflighted. dev-2-42 must be reachable (else the whole spec skips).
// If dev-2-41 is not serving (im.dhis2.org instances hibernate -> 503), the run degrades: the
// client-side proofs (auto-switch banner, /api/assistants list, target-scoped chat body) still hold
// because they are driven by heim's config, not by play41's health — only the play41 server round-trips
// (its "Verified." and a successful tool result) are skipped with a clear message.

import { mkdirSync } from "node:fs";
import path from "node:path";
import { type Page } from "@playwright/test";
import { test, expect, openPanel, seedSettings, grantHostPermission } from "../fixtures";
import {
  LIVE_PORT,
  PLAY_BASE_URL,
  PLAY41_BASE_URL,
  LIVE_MULTI_MODEL,
  MULTI_SYSTEM_PROMPT,
  MULTI_TARGETS,
  preflight,
  preflightUsable,
  startLiveServer,
  warmBridge,
  type LiveServer,
} from "./liveServer";

const BASE_URL = `http://127.0.0.1:${LIVE_PORT}`;
const MATCH_TEXT = "This tab matches the assistant target.";
const SHOTS = process.env.HEIM_DRIVE_SHOTS ?? "";

let shotIndex = 0;
async function shot(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  shotIndex += 1;
  await page.screenshot({ path: path.join(SHOTS, `${String(shotIndex).padStart(2, "0")}-${name}.png`) });
}

async function gotoTab(tab: Page, url: string): Promise<void> {
  await tab.goto(url, { timeout: 60_000, waitUntil: "domcontentloaded" }).catch(() => {});
  await tab.bringToFront();
}

let skipReason: string | null = null;
let play41Up = false;
let server: LiveServer | null = null;

test.describe.serial("multi-target registry against real heim + play42/play41", () => {
  test.beforeAll(async () => {
    // The primary must be reachable at all; a redirect-to-login (302) counts as up.
    skipReason = await preflight(PLAY_BASE_URL);
    if (skipReason) return;
    // The second target is preflighted strictly: a hibernated 503 is "down" (would fail verify/tool
    // calls), so we degrade rather than fail its server round-trips.
    const p41 = await preflightUsable(PLAY41_BASE_URL);
    play41Up = p41 === null;
    if (!play41Up) {
      console.log(`[multitarget] dev-2-41 not serving -> degraded (client-side proofs only): ${p41}`);
    }
    warmBridge();
  });

  test.afterAll(async () => {
    if (server) await server.stop();
    server = null;
  });

  test("auto-switch, per-target verify, target-scoped chat", async ({ context, extensionId }) => {
    test.skip(skipReason !== null, skipReason ?? "");
    test.setTimeout(1_800_000); // cold model load + two tool-using turns; the model is the long pole

    try {
      await seedSettings(context, extensionId, { baseUrl: BASE_URL, token: "" });
      const panel = await openPanel(context, extensionId);
      server = startLiveServer(extensionId, {
        model: LIVE_MULTI_MODEL,
        systemPrompt: MULTI_SYSTEM_PROMPT,
        targets: MULTI_TARGETS,
      });
      const composer = panel.getByPlaceholder(/Message \(Enter to send/);
      await expect(composer).toBeVisible({ timeout: 600_000 });

      // The e2e build statically pre-grants play.im.dhis2.org/* (origin-wide, so it covers BOTH
      // /dev-2-42 and /dev-2-41); the grant resolves silently.
      const granted = await grantHostPermission(panel, new URL(PLAY_BASE_URL).origin);
      expect(granted, "run with the e2e build: `bun run build:e2e` + HEIM_E2E_BUILD=1").toBe(true);

      // The registry lists BOTH targets — the multi-target contract at the API boundary. Fetched from
      // node (no browser CORS), mirroring actasyou's direct /api/chat check.
      const listRes = await fetch(`${BASE_URL}/api/assistants`);
      expect(listRes.ok).toBe(true);
      const list = (await listRes.json()) as { targets?: Array<{ id?: string; name?: string }> };
      const ids = (list.targets ?? []).map((t) => t.id);
      console.log(`[multitarget] /api/assistants ids: ${JSON.stringify(ids)}`);
      expect(ids).toContain("play42");
      expect(ids).toContain("play41");
      expect(list.targets?.length).toBe(2);

      // (1) A tab under /dev-2-42 -> the banner selects play42 and reports the tab matches.
      const tab = await context.newPage();
      await gotoTab(tab, `${PLAY_BASE_URL}/dhis-web-dashboard/`);
      await expect(panel.getByText(MATCH_TEXT)).toBeVisible({ timeout: 60_000 });
      await expect(panel.getByText("play42", { exact: true })).toBeVisible({ timeout: 30_000 });
      await shot(panel, "on-play42");

      // Per-target verify over play42's own profile-verify tool (play42__dhis2_cli against /dev-2-42).
      await panel.getByRole("button", { name: "Verify" }).click();
      await expect(panel.getByText("Verified.")).toBeVisible({ timeout: 180_000 });
      await shot(panel, "play42-verified");

      // (2) THE HEADLINE: navigate the SAME tab to /dev-2-41 -> the banner AUTO-SWITCHES to play41
      // (same origin, longer-path target wins) with no click. Assert the name swap in both directions.
      await gotoTab(tab, `${PLAY41_BASE_URL}/dhis-web-dashboard/`);
      await expect(panel.getByText("play41", { exact: true })).toBeVisible({ timeout: 60_000 });
      await expect(panel.getByText("play42", { exact: true })).toHaveCount(0); // swapped away entirely
      await expect(panel.getByText(MATCH_TEXT)).toBeVisible({ timeout: 30_000 });
      await expect(panel.getByTestId("target-picker")).toHaveCount(0); // distinct paths -> no tie
      await shot(panel, "auto-switched-play41");

      // Per-target verify on play41 — only when the instance is actually serving (its verify hits
      // /dev-2-41 for real). On the degraded path this round-trip would 503, so skip just this.
      if (play41Up) {
        await panel.getByRole("button", { name: "Verify" }).click();
        await expect(panel.getByText("Verified.")).toBeVisible({ timeout: 180_000 });
        await shot(panel, "play41-verified");
      } else {
        console.log("[multitarget] skip play41 Verified. assertion (dev-2-41 not serving)");
      }

      // (3) Target-scoped chat while on /dev-2-41: intercept POST /api/chat and prove `target:"play41"`
      // rides the turn (client-side — holds even when the tool call itself can't reach a dead play41).
      let chatBody: string | null = null;
      await panel.route("**/api/chat", async (route) => {
        if (chatBody === null) chatBody = route.request().postData();
        await route.continue();
      });
      await composer.fill("How many organisation units are there? Use the dhis2 tools.");
      await panel.getByRole("button", { name: "Send" }).click();
      await expect.poll(() => chatBody, { timeout: 60_000 }).not.toBeNull();
      const parsed = JSON.parse(chatBody as unknown as string) as { target?: unknown };
      console.log(`[multitarget] POST /api/chat target = ${JSON.stringify(parsed.target)}`);
      expect(parsed.target).toBe("play41"); // the turn is scoped to play41

      // The play41-namespaced tool drives the turn. On the serving path this is a hard proof (call +
      // result chip, and the call names the play41 bridge tool); degraded, the call may error against a
      // dead instance, so we observe best-effort and lean on the server log for the play41 tool name.
      if (play41Up) {
        await expect(panel.getByTestId("tool-chip-call").first()).toBeVisible({ timeout: 300_000 });
        await expect(panel.getByTestId("tool-chip-result").first()).toBeVisible({ timeout: 300_000 });
        const callDetail = (await panel.getByTestId("tool-chip-call").first().innerText())
          .replace(/\s+/g, " ")
          .trim();
        console.log(`[multitarget] play41 tool call: ${callDetail}`);
        expect(callDetail).toMatch(/play41/); // the play41-scoped tool, not play42's
      } else {
        const sawCall = await panel
          .getByTestId("tool-chip-call")
          .first()
          .isVisible()
          .catch(() => false);
        // Let a tool call surface if the model attempts one against the dead instance (it still names
        // the play41 tool before the result errors).
        const appeared = sawCall
          ? true
          : await panel
              .getByTestId("tool-chip-call")
              .first()
              .waitFor({ state: "visible", timeout: 120_000 })
              .then(() => true)
              .catch(() => false);
        if (appeared) {
          const callDetail = (await panel.getByTestId("tool-chip-call").first().innerText())
            .replace(/\s+/g, " ")
            .trim();
          console.log(`[multitarget] play41 tool call (degraded): ${callDetail}`);
          expect(callDetail).toMatch(/play41/);
        } else {
          console.log("[multitarget] no tool chip on degraded path; target-scoping proven via chat body");
        }
      }
      await shot(panel, "chat-target-play41");
      await panel.unroute("**/api/chat");

      // (4) A tab under a different origin -> the assistant is irrelevant to this page, so the banner
      // collapses to the primary-name one-liner.
      await gotoTab(tab, "https://example.com/");
      await expect(panel.getByText("Not a play42 page.")).toBeVisible({ timeout: 30_000 });
      await shot(panel, "non-matching-example");

      // The heim serve log should show the play41 bridge/tool touched by this turn (belt-and-braces
      // alongside the panel proofs above).
      console.log(`[multitarget] heim serve log tail:\n${server.tailLog(40)}`);

      await tab.close();
      await panel.close();
    } catch (err) {
      if (server) console.log(`[multitarget] heim serve log tail:\n${server.tailLog(80)}`);
      throw err;
    }
  });
});
