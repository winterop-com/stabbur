// Deterministic docs screenshots for the Chrome side panel.
//
//   bun run screenshots        (from extension/, wraps `bun run build` + `build:dhis2` + this)
//   bun run e2e/screenshots.ts (this script alone, expects both builds present)
//
// Two kinds of images are produced:
//
//   1. Panel-detail shots (01-08) — the panel driven through each documented state against MOCK
//      backends on ephemeral ports, written panel-sized to docs/img/extension/NN-name.png. These
//      use the DHIS2-flavored build (.output/chrome-mv3-dhis2) so the header reads "kodo for
//      DHIS2"; the mock machinery is fixtures.ts + mockServer.ts, exactly the mock E2E tier's.
//
//   2. Hero shots (hero-1..3) — the product story: the panel docked NEXT TO the real DHIS2
//      instance. Playwright can't dock the native side panel, so each hero is an HONEST COMPOSITE
//      of two real screenshots — the live play42 UI (~1100x800) on the left and the panel
//      (400x800) on the right, joined by a thin divider. Both halves are real pixels; only the
//      side-by-side arrangement is synthesized. The panel's target banner / Who-am-I run against
//      the REAL logged-in play42 tab (mock kodo backend, real probe), so the identity shown is
//      genuinely read from play42. If play42 is unreachable the page half falls back to the
//      TargetSiteMock (noted in the log) rather than failing the run.
//
// It is intentionally hermetic and repeatable for the panel: no real `kodo serve`, no fixed port,
// a pinned viewport, and a forced light theme so re-running produces stable framing.

import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, type BrowserContext, type Page } from "@playwright/test";

import {
  EXTENSION_PATH,
  PANEL_PATH,
  grantHostPermission,
  resolveExtensionId,
  scratchRoot,
  seedSettings,
  userDataDir,
} from "./fixtures";
import { KodoMock, TargetSiteMock, bindAssistant, reservePort, type ChatFrame } from "./mockServer";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..");
const OUT_DIR = path.join(REPO_ROOT, "docs", "img", "extension");
const VIEWPORT = { width: 400, height: 700 };
const HERO_PANEL = { width: 400, height: 800 };
const HERO_PAGE = { width: 1100, height: 800 };

// The real public DHIS2 demo used for the hero page half (and the real session probe). Kept in
// sync with e2e/live/liveServer.ts by convention; defined locally so this script stays standalone.
const PLAY_BASE_URL = "https://play.im.dhis2.org/dev-2-42";
const PLAY_ORIGIN = new URL(PLAY_BASE_URL).origin;

// Prefer the DHIS2-flavored build for the branded panel; fall back to the generic build.
const DHIS2_EXTENSION_PATH = path.resolve(HERE, "..", ".output", "chrome-mv3-dhis2");

/** Wipe all persisted state so one scenario's binding record / transcript never leaks into the next. */
async function resetStorage(context: BrowserContext, extensionId: string): Promise<void> {
  const page = await context.newPage();
  await page.goto(`chrome-extension://${extensionId}/${PANEL_PATH}`);
  await page.evaluate(async () => {
    await chrome.storage.local.clear();
    localStorage.clear();
  });
  await page.close();
}

/** Open a fresh side panel page (light theme forced) at the given viewport and wait for render. */
async function openPanel(
  context: BrowserContext,
  extensionId: string,
  viewport: { width: number; height: number } = VIEWPORT,
): Promise<Page> {
  const page = await context.newPage();
  await page.setViewportSize(viewport);
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto(`chrome-extension://${extensionId}/${PANEL_PATH}`);
  await page.waitForLoadState("domcontentloaded");
  // Substring name match — the DHIS2 build's heading reads "kodo for DHIS2".
  await page.getByRole("heading", { name: "kodo" }).first().waitFor({ timeout: 15_000 });
  return page;
}

/** Strip any residual .dark class, let layout settle, and write a viewport-sized PNG to `file`. */
async function snapTo(page: Page, file: string): Promise<void> {
  await page.emulateMedia({ colorScheme: "light" });
  await page.evaluate(() => document.documentElement.classList.remove("dark"));
  await page.waitForTimeout(300);
  await page.screenshot({ path: file, fullPage: false });
}

/** Panel-detail shot NN-name.png in docs/img/extension. */
async function snap(page: Page, name: string): Promise<void> {
  const file = path.join(OUT_DIR, `${name}.png`);
  await snapTo(page, file);
  console.log(`  wrote ${path.relative(REPO_ROOT, file)}`);
}

/** Is the play demo reachable? (Best-effort; a non-5xx answer counts.) */
async function playReachable(): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15_000);
  try {
    const r = await fetch(`${PLAY_BASE_URL}/api/system/info.json`, { signal: controller.signal });
    return r.status < 500;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/** Log into play42 in the given tab (JSON /api/auth/login, admin/district) and confirm the session. */
async function loginPlay(tab: Page): Promise<boolean> {
  await tab.goto(`${PLAY_BASE_URL}/`, { timeout: 60_000, waitUntil: "domcontentloaded" }).catch(() => {});
  const res = await tab.evaluate(async (base: string) => {
    try {
      await fetch(`${base}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ username: "admin", password: "district" }),
      });
      const me = await fetch(`${base}/api/me.json?fields=name,username`, { headers: { Accept: "application/json" } });
      return me.ok;
    } catch {
      return false;
    }
  }, PLAY_BASE_URL);
  return res;
}

/** Navigate a tab to a play42 URL and let its charts/widgets settle before a screenshot. */
async function settlePlay(tab: Page, url: string): Promise<void> {
  await tab.goto(url, { timeout: 60_000, waitUntil: "domcontentloaded" }).catch(() => {});
  await tab.waitForLoadState("networkidle", { timeout: 30_000 }).catch(() => {});
  await tab.waitForTimeout(4000);
}

/**
 * Composite a left (page) PNG and a right (panel) PNG side by side with a thin divider, and write
 * the result to docs/img/extension/{name}.png. Both images are embedded as data URIs and rendered
 * at a fixed 800px height; the joined `#frame` element is screenshotted so the crop is exact.
 */
async function composite(context: BrowserContext, leftPng: string, rightPng: string, name: string): Promise<void> {
  const left = readFileSync(leftPng).toString("base64");
  const right = readFileSync(rightPng).toString("base64");
  const html = `<!doctype html><html><head><meta charset="utf-8"><style>
    *{margin:0;padding:0;box-sizing:border-box}
    html,body{background:#e2e8f0}
    #frame{display:inline-flex;align-items:stretch;background:#fff}
    #frame img{display:block;height:800px;width:auto}
    #frame img.left{border-right:1px solid #cbd5e1}
    #frame img.right{box-shadow:-10px 0 28px rgba(15,23,42,.14)}
  </style></head><body>
    <div id="frame">
      <img class="left" src="data:image/png;base64,${left}">
      <img class="right" src="data:image/png;base64,${right}">
    </div>
  </body></html>`;
  const page = await context.newPage();
  await page.setViewportSize({ width: 1560, height: 840 });
  await page.emulateMedia({ colorScheme: "light" });
  await page.setContent(html, { waitUntil: "load" });
  await page.waitForTimeout(200);
  const file = path.join(OUT_DIR, `${name}.png`);
  await page.locator("#frame").screenshot({ path: file });
  await page.close();
  console.log(`  wrote ${path.relative(REPO_ROOT, file)}`);
}

/**
 * Try to render the fully-real hero-2 panel: assistant pointed at play42, the real signed-in play
 * tab active (so the banner genuinely matches), and the panel's own probe reading the live identity.
 * Returns true on success, false if the host-permission grant can't complete (headless prompt) so
 * the caller can fall back to the mock-driven panel.
 */
async function tryRealHero2Panel(
  context: BrowserContext,
  extensionId: string,
  playTab: Page,
  panelPng: string,
): Promise<boolean> {
  await resetStorage(context, extensionId);
  const kodo = new KodoMock();
  await kodo.start();
  try {
    kodo.state.phase = "ready";
    kodo.state.assistant = bindAssistant(PLAY_BASE_URL);
    await seedSettings(context, extensionId, {
      backends: [{ id: "default", name: "Default", baseUrl: kodo.baseUrl(), token: "" }],
      activeBackendId: "default",
    });
    const panel = await openPanel(context, extensionId, HERO_PANEL);
    await panel.getByPlaceholder(/Message \(Enter to send/).waitFor({ timeout: 15_000 });
    // Grant host access while the PANEL is the foreground tab (valid user gesture). May fail
    // headless — the caller then uses the mock path.
    await panel.bringToFront();
    if (!(await grantHostPermission(panel, PLAY_ORIGIN))) {
      throw new Error(`failed to grant host permission for ${PLAY_ORIGIN}`);
    }
    await playTab.bringToFront();
    await panel.getByText("This tab matches the assistant target.").waitFor({ timeout: 20_000 });
    await panel.getByRole("button", { name: "Who am I here?" }).click();
    await panel.getByText(/Browsing as /).waitFor({ timeout: 20_000 });
    await snapTo(panel, panelPng);
    await panel.close();
    console.log("  hero-2: used the REAL play42 probe (matched banner + live Who-am-I).");
    return true;
  } catch (err) {
    console.warn(`  hero-2: real probe unavailable (${err instanceof Error ? err.message : String(err)}); using mock.`);
    return false;
  } finally {
    await kodo.stop();
  }
}

/** Render the hero-2 panel against the TargetSiteMock (matched mock tab + real-valued Who-am-I). */
async function mockHero2Panel(
  context: BrowserContext,
  extensionId: string,
  target: TargetSiteMock,
  panelPng: string,
): Promise<void> {
  await resetStorage(context, extensionId);
  const kodo = new KodoMock();
  await kodo.start();
  try {
    kodo.state.phase = "ready";
    kodo.state.assistant = bindAssistant(target.baseUrl());
    await seedSettings(context, extensionId, {
      backends: [{ id: "default", name: "Default", baseUrl: kodo.baseUrl(), token: "" }],
      activeBackendId: "default",
    });
    const panel = await openPanel(context, extensionId, HERO_PANEL);
    await panel.getByPlaceholder(/Message \(Enter to send/).waitFor({ timeout: 15_000 });
    const tab = await context.newPage();
    await tab.goto(`${target.baseUrl()}/dhis`);
    await tab.bringToFront();
    await panel.getByText("This tab matches the assistant target.").waitFor({ timeout: 15_000 });
    await panel.getByRole("button", { name: "Who am I here?" }).click();
    await panel.getByText(/Browsing as /).waitFor({ timeout: 15_000 });
    await snapTo(panel, panelPng);
    await tab.close();
    await panel.close();
  } finally {
    await kodo.stop();
  }
}

async function main(): Promise<void> {
  const extPath = existsSync(DHIS2_EXTENSION_PATH) ? DHIS2_EXTENSION_PATH : EXTENSION_PATH;
  if (!existsSync(extPath)) {
    throw new Error(`Built extension not found at ${extPath}. Run \`bun run build:dhis2\` first.`);
  }
  if (extPath !== DHIS2_EXTENSION_PATH) {
    console.warn(`note: DHIS2 build missing, using generic build at ${extPath} (panel branding will read "kodo").`);
  }
  mkdirSync(OUT_DIR, { recursive: true });
  const scratchDir = mkdtempSync(path.join(scratchRoot(), "kodo-ext-heroshots-"));

  const dir = userDataDir("kodo-ext-shots-");
  const context = await chromium.launchPersistentContext(dir, {
    channel: "chromium",
    headless: process.env.HEADED !== "1",
    viewport: VIEWPORT,
    colorScheme: "light",
    args: [
      `--disable-extensions-except=${extPath}`,
      `--load-extension=${extPath}`,
      "--no-first-run",
      "--no-default-browser-check",
    ],
  });
  const extensionId = await resolveExtensionId(context);
  console.log(`extension id: ${extensionId} (build: ${path.basename(extPath)})`);

  try {
    // 01 — connection gate, disconnected (server not listening on the seeded port).
    {
      await resetStorage(context, extensionId);
      const port = await reservePort();
      await seedSettings(context, extensionId, {
        backends: [{ id: "default", name: "Default", baseUrl: `http://127.0.0.1:${port}`, token: "" }],
        activeBackendId: "default",
      });
      const panel = await openPanel(context, extensionId);
      await panel.getByText(/kodo is not reachable/).waitFor({ timeout: 15_000 });
      await snap(panel, "01-connection-disconnected");
      await panel.close();
    }

    // 02 / 03 — chat with a streamed answer + a JSON tool chip (collapsed, then expanded).
    {
      await resetStorage(context, extensionId);
      const kodo = new KodoMock();
      await kodo.start();
      try {
        kodo.state.phase = "ready";
        const frames: ChatFrame[] = [
          { type: "token", text: "I checked the DHIS2 instance for you.\n\n" },
          { type: "tool", kind: "call", detail: "dhis2__dhis2_cli(GET /api/organisationUnits?fields=id&paging=false)" },
          {
            type: "tool",
            kind: "result",
            detail: '{"exit_code":0,"stdout":"{\\"pager\\":{\\"total\\":1332},\\"organisationUnits\\":[]}"}',
          },
          { type: "token", text: "There are **1332** organisation units in the hierarchy." },
          { type: "done" },
        ];
        kodo.state.chatFrames = frames;
        kodo.state.chatGapMs = 20;
        await seedSettings(context, extensionId, {
          backends: [{ id: "default", name: "Default", baseUrl: kodo.baseUrl(), token: "" }],
          activeBackendId: "default",
        });
        const panel = await openPanel(context, extensionId);
        await panel.getByPlaceholder(/Message \(Enter to send/).waitFor({ timeout: 15_000 });
        await panel.getByPlaceholder(/Message \(Enter to send/).fill("How many org units are in the system?");
        await panel.getByRole("button", { name: "Send" }).click();
        await panel.getByTestId("tool-chip-result").waitFor({ timeout: 15_000 });
        await panel.getByRole("button", { name: "Send" }).waitFor({ timeout: 15_000 });
        await snap(panel, "02-chat-tool-collapsed");

        await panel.getByTestId("tool-chip-result").locator("summary").click();
        await panel.getByTestId("tool-chip-result").locator("pre").waitFor({ timeout: 10_000 });
        await snap(panel, "03-chat-tool-expanded");
        await panel.close();
      } finally {
        await kodo.stop();
      }
    }

    // 04 / 05 / 06 — assistant target banner + the "Use my login" bind flow.
    {
      const kodo = new KodoMock();
      const target = new TargetSiteMock();
      await kodo.start();
      await target.start();
      try {
        kodo.state.phase = "ready";
        target.infoBody = { version: "2.42", systemName: "Play Sierra Leone" };
        target.meBody = { name: "Admin User", username: "admin" };

        await resetStorage(context, extensionId);
        kodo.state.assistant = bindAssistant(target.baseUrl());
        await seedSettings(context, extensionId, {
          backends: [{ id: "default", name: "Default", baseUrl: kodo.baseUrl(), token: "" }],
          activeBackendId: "default",
        });
        const panel = await openPanel(context, extensionId);
        await panel.getByPlaceholder(/Message \(Enter to send/).waitFor({ timeout: 15_000 });
        const tab = await context.newPage();
        await tab.goto(`${target.baseUrl()}/dhis`);
        await tab.bringToFront();
        await panel.getByTestId("bind-use-my-login").waitFor({ timeout: 15_000 });
        await panel.getByText("This tab matches the assistant target.").waitFor({ timeout: 15_000 });
        await panel.getByRole("button", { name: "Who am I here?" }).click();
        await panel.getByText(/Admin User/).waitFor({ timeout: 15_000 });
        await snap(panel, "04-target-unbound");

        await panel.getByTestId("bind-use-my-login").click();
        await panel.getByTestId("bind-consent").waitFor({ timeout: 10_000 });
        await panel.getByText(/read-only \(GET\)/).waitFor({ timeout: 10_000 });
        await snap(panel, "05-bind-consent");

        await panel.getByTestId("bind-confirm").click();
        await panel.getByTestId("bind-acting-as").waitFor({ timeout: 15_000 });
        await snap(panel, "06-bind-acting-as");
        await tab.close();
        await panel.close();
      } finally {
        await kodo.stop();
        await target.stop();
      }
    }

    // 07 — backend switcher with two backends.
    {
      await resetStorage(context, extensionId);
      const a = new KodoMock();
      const b = new KodoMock();
      await a.start();
      await b.start();
      try {
        a.state.phase = "ready";
        b.state.phase = "ready";
        a.state.assistant = { name: "generic", base_url: a.baseUrl(), auth: "none", can_verify: false, verified: null };
        b.state.assistant = bindAssistant(b.baseUrl());
        await seedSettings(context, extensionId, {
          backends: [
            { id: "a", name: "Local (generic)", baseUrl: a.baseUrl(), token: "" },
            { id: "b", name: "play42 assistant", baseUrl: b.baseUrl(), token: "" },
          ],
          activeBackendId: "b",
        });
        const panel = await openPanel(context, extensionId);
        await panel.getByTestId("backend-switcher").waitFor({ timeout: 15_000 });
        await panel.getByText("play42", { exact: true }).waitFor({ timeout: 15_000 });
        await panel.getByTestId("backend-switcher").focus();
        await snap(panel, "07-backend-switcher");
        await panel.close();
      } finally {
        await a.stop();
        await b.stop();
      }
    }

    // 08 — settings: the backends editor + the exact cors_origins line for this extension id.
    {
      await resetStorage(context, extensionId);
      await seedSettings(context, extensionId, {
        backends: [
          { id: "a", name: "Local (generic)", baseUrl: "http://127.0.0.1:8000", token: "" },
          { id: "b", name: "play42 assistant", baseUrl: "http://127.0.0.1:4599", token: "" },
        ],
        activeBackendId: "a",
        pageContextEnabled: true,
        pageTextEnabled: false,
      });
      const panel = await openPanel(context, extensionId);
      await panel.getByLabel("Settings").click();
      await panel.getByRole("heading", { name: "Settings" }).waitFor({ timeout: 10_000 });
      await panel.getByTestId("backend-entry").first().waitFor({ timeout: 10_000 });
      await snap(panel, "08-settings-backends");
      await panel.close();
    }

    console.log("\nPanel-detail shots written. Building hero composites...");
    await heroShots(context, extensionId, scratchDir);
    console.log("\nAll screenshots written to docs/img/extension/.");
  } finally {
    for (const p of context.pages()) await p.close().catch(() => {});
    await context.close().catch(() => {});
    rmSync(dir, { recursive: true, force: true });
    rmSync(scratchDir, { recursive: true, force: true });
  }
}

/**
 * The three hero composites: the panel docked next to the real play42 instance. Captures the play
 * page halves once (login page, then dashboard), keeps the logged-in dashboard tab open for the
 * real Who-am-I in hero-2, drives each panel state against a mock kodo, and composites. Resilient:
 * a play outage falls back to the TargetSiteMock page half; a single hero failure is logged and the
 * others still run.
 */
async function heroShots(context: BrowserContext, extensionId: string, scratchDir: string): Promise<void> {
  const reachable = await playReachable();
  const target = new TargetSiteMock();
  await target.start();
  target.infoBody = { version: "2.42", systemName: "Play Sierra Leone" };
  target.meBody = { name: "Admin User", username: "admin" };

  const loginPng = path.join(scratchDir, "play-login.png");
  const dashPng = path.join(scratchDir, "play-dashboard.png");
  let playTab: Page | null = null;
  let havePlay = false;

  try {
    if (reachable) {
      // Capture the login page BEFORE authenticating (page half for the consent hero), then log in
      // and capture the dashboard (page half for the chat + target heroes).
      playTab = await context.newPage();
      await playTab.setViewportSize(HERO_PAGE);
      await settlePlay(playTab, `${PLAY_BASE_URL}/dhis-web-login/`);
      await playTab.screenshot({ path: loginPng, fullPage: false });
      const ok = await loginPlay(playTab);
      if (ok) {
        await settlePlay(playTab, `${PLAY_BASE_URL}/dhis-web-dashboard/`);
        await playTab.screenshot({ path: dashPng, fullPage: false });
        havePlay = true;
      } else {
        console.warn("note: play42 login failed; hero page halves fall back to the TargetSiteMock.");
      }
    } else {
      console.warn("note: play42 unreachable; hero page halves fall back to the TargetSiteMock.");
    }

    // Fallback page halves: screenshot the TargetSiteMock's stub pages (real, if minimal, pixels).
    if (!havePlay) {
      const t = await context.newPage();
      await t.setViewportSize(HERO_PAGE);
      await t.goto(`${target.baseUrl()}/dhis-web-login/`).catch(() => {});
      await t.waitForTimeout(200);
      await t.screenshot({ path: loginPng, fullPage: false });
      await t.goto(`${target.baseUrl()}/dhis-web-dashboard/`).catch(() => {});
      await t.waitForTimeout(200);
      await t.screenshot({ path: dashPng, fullPage: false });
      await t.close();
    }

    // hero-1 — dashboard + panel chatting about the page (page context on).
    try {
      await resetStorage(context, extensionId);
      const kodo = new KodoMock();
      await kodo.start();
      try {
        kodo.state.phase = "ready";
        kodo.state.chatFrames = [
          { type: "token", text: "This is the DHIS2 demo dashboard. The visible widgets cover:\n\n" },
          {
            type: "token",
            text: "- ANC and immunization coverage trends\n- Staffing and commodity stock levels\n- Recent data-entry completeness by district\n\n",
          },
          { type: "token", text: "Ask me to pull the underlying numbers for any of these with the DHIS2 tools." },
          { type: "done" },
        ];
        kodo.state.chatGapMs = 15;
        await seedSettings(context, extensionId, {
          backends: [{ id: "default", name: "Default", baseUrl: kodo.baseUrl(), token: "" }],
          activeBackendId: "default",
          pageContextEnabled: true,
          pageTextEnabled: false,
        });
        const panel = await openPanel(context, extensionId, HERO_PANEL);
        const composer = panel.getByPlaceholder(/Message \(Enter to send/);
        await composer.waitFor({ timeout: 15_000 });
        await composer.fill("What is this dashboard showing? Summarize the key indicators.");
        await panel.getByRole("button", { name: "Send" }).click();
        await panel.getByText(/completeness by district/).waitFor({ timeout: 15_000 });
        await panel.getByRole("button", { name: "Send" }).waitFor({ timeout: 15_000 });
        const panelPng = path.join(scratchDir, "panel-hero-1.png");
        await snapTo(panel, panelPng);
        await composite(context, dashPng, panelPng, "hero-1-dashboard");
        await panel.close();
      } finally {
        await kodo.stop();
      }
    } catch (err) {
      console.warn(`note: hero-1 failed: ${err instanceof Error ? err.message : String(err)}`);
    }

    // hero-2 — dashboard + target banner matched + Who-am-I identity.
    //
    // Best case (real play + a grantable host permission): the assistant points at play42, the real
    // signed-in play tab is the active tab, and the panel's own probe reads the live identity — all
    // real. The optional-host-permission grant needs an interactive prompt that headless Chromium
    // can't answer, so we try it briefly and, on failure, fall back to driving the panel against the
    // TargetSiteMock (a matched mock tab whose /api/me + /api/system/info mirror play42's admin) —
    // still real panel pixels, composited with the real play42 dashboard page half.
    try {
      const panelPng = path.join(scratchDir, "panel-hero-2.png");
      const real = havePlay && playTab ? await tryRealHero2Panel(context, extensionId, playTab, panelPng) : false;
      if (!real) await mockHero2Panel(context, extensionId, target, panelPng);
      await composite(context, dashPng, panelPng, "hero-2-target");
    } catch (err) {
      console.warn(`note: hero-2 failed: ${err instanceof Error ? err.message : String(err)}`);
    }

    // hero-3 — login page + the bind consent card (driven against the TargetSiteMock, per design:
    // the consent close-up needs a tab match, which the mock target provides deterministically).
    try {
      await resetStorage(context, extensionId);
      const kodo = new KodoMock();
      await kodo.start();
      try {
        kodo.state.phase = "ready";
        kodo.state.assistant = bindAssistant(target.baseUrl());
        await seedSettings(context, extensionId, {
          backends: [{ id: "default", name: "Default", baseUrl: kodo.baseUrl(), token: "" }],
          activeBackendId: "default",
        });
        const panel = await openPanel(context, extensionId, HERO_PANEL);
        await panel.getByPlaceholder(/Message \(Enter to send/).waitFor({ timeout: 15_000 });
        const tab = await context.newPage();
        await tab.goto(`${target.baseUrl()}/dhis`);
        await tab.bringToFront();
        await panel.getByTestId("bind-use-my-login").waitFor({ timeout: 15_000 });
        await panel.getByText("This tab matches the assistant target.").waitFor({ timeout: 15_000 });
        await panel.getByTestId("bind-use-my-login").click();
        await panel.getByTestId("bind-consent").waitFor({ timeout: 10_000 });
        await panel.getByText(/read-only \(GET\)/).waitFor({ timeout: 10_000 });
        const panelPng = path.join(scratchDir, "panel-hero-3.png");
        await snapTo(panel, panelPng);
        await composite(context, loginPng, panelPng, "hero-3-consent");
        await tab.close();
        await panel.close();
      } finally {
        await kodo.stop();
      }
    } catch (err) {
      console.warn(`note: hero-3 failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  } finally {
    if (playTab) await playTab.close().catch(() => {});
    await target.stop();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
