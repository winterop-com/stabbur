// Shared Playwright fixtures for the extension E2E tiers. Launches a persistent
// Chromium context with the built MV3 extension loaded, resolves the extension id
// from its service worker, and exposes helpers to seed chrome.storage settings and
// open the side panel page.
//
// Extension loading requires a real (non-incognito) profile, so we use
// launchPersistentContext with a fresh scratchpad user-data dir. Modern Chromium
// supports extensions under the new headless mode (channel "chromium"); set
// HEADED=1 to force a visible window if a given machine can't run headless
// extensions.

import { spawnSync } from "node:child_process";
import { existsSync, statSync } from "node:fs";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  test as base,
  chromium,
  type BrowserContext,
  type Page,
  type Worker,
} from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));

const SCRATCH =
  process.env.HEIM_E2E_SCRATCH ??
  "/private/tmp/claude-502/-Users-morteoh-dev-local-heim/180a1f72-7889-42d9-bb03-f191e8f9cc1f/scratchpad";

/**
 * Absolute path to the built, unpacked extension. The live tier needs the TEST-ONLY e2e build
 * (`.output/chrome-mv3-e2e`, from `bun run build:e2e`) whose static host_permissions pre-grant the
 * target origins so the headless mint tail can run. The NEWEST build wins: each tier builds its
 * own flavor right before running (`e2e` -> generic, `e2e:live` -> e2e build), so comparing build
 * times routes each tier to its fresh output — and a lingering e2e dir from an earlier
 * `build:e2e`/screenshots run can never shadow a newer generic build with stale code (the two
 * flavors are only byte-identical until the next source change).
 */
const E2E_EXTENSION_PATH = path.resolve(HERE, "..", ".output", "chrome-mv3-e2e");
const GENERIC_EXTENSION_PATH = path.resolve(HERE, "..", ".output", "chrome-mv3");

function builtAt(dir: string): number {
  try {
    return statSync(path.join(dir, "manifest.json")).mtimeMs;
  } catch {
    return -1;
  }
}

export const EXTENSION_PATH =
  builtAt(E2E_EXTENSION_PATH) >= builtAt(GENERIC_EXTENSION_PATH) ? E2E_EXTENSION_PATH : GENERIC_EXTENSION_PATH;

const HEADED = process.env.HEADED === "1" || process.env.HEADED === "true";

/** Root for throwaway user-data dirs: the session scratchpad if present, else the OS tmp dir. */
export function scratchRoot(): string {
  return existsSync(SCRATCH) ? SCRATCH : tmpdir();
}

/** A fresh, uniquely-named user-data dir under the scratch root (caller cleans it up). */
export function userDataDir(prefix = "heim-ext-e2e-"): string {
  return mkdtempSync(path.join(scratchRoot(), prefix));
}

export async function resolveExtensionId(context: BrowserContext): Promise<string> {
  // The MV3 background is a service worker; it may already be running or arrive
  // shortly after launch.
  let worker: Worker | undefined = context.serviceWorkers()[0];
  if (!worker) {
    worker = await context.waitForEvent("serviceworker", { timeout: 30_000 });
  }
  const url = worker.url(); // chrome-extension://<id>/background.js
  const match = /chrome-extension:\/\/([a-z]+)\//.exec(url);
  if (!match) throw new Error(`could not parse extension id from ${url}`);
  return match[1];
}

async function launch(): Promise<{ context: BrowserContext; dir: string }> {
  if (!existsSync(EXTENSION_PATH)) {
    throw new Error(
      `Built extension not found at ${EXTENSION_PATH}. Run \`bun run build\` first (the e2e scripts do this).`,
    );
  }
  const dir = userDataDir();
  const args = [
    `--disable-extensions-except=${EXTENSION_PATH}`,
    `--load-extension=${EXTENSION_PATH}`,
    "--no-first-run",
    "--no-default-browser-check",
  ];
  const context = await chromium.launchPersistentContext(dir, {
    channel: "chromium",
    headless: !HEADED,
    args,
  });
  return { context, dir };
}

interface ExtFixtures {
  context: BrowserContext;
  extensionId: string;
}

export const test = base.extend<ExtFixtures>({
  context: async ({}, use) => {
    const { context, dir } = await launch();
    await use(context);
    // After long tests the MV3 service worker goes dormant and context.close() can hang
    // indefinitely (observed 15+ min: chromium idle at 0% CPU, runner waiting). Close pages
    // first, race close against a deadline, then hard-kill any chromium still holding this
    // run's unique profile dir - a no-op whenever close behaves.
    for (const p of context.pages()) await p.close().catch(() => {});
    const closed = await Promise.race([
      context.close().then(() => true),
      new Promise<boolean>((resolve) => setTimeout(() => resolve(false), 30_000)),
    ]);
    if (!closed) spawnSync("pkill", ["-f", dir]);
    rmSync(dir, { recursive: true, force: true });
  },
  extensionId: async ({ context }, use) => {
    await use(await resolveExtensionId(context));
  },
});

export const PANEL_PATH = "sidepanel.html";

/**
 * Translate a legacy flat {baseUrl, token} seed into the v2 backends shape. The extension is
 * v2-only (no migrate-on-read), so the flat-shape compatibility lives here, in one place: every
 * existing spec that seeds {baseUrl, token, ...toggles} keeps working. A patch that already
 * carries a `backends` array is passed through untouched.
 */
function toV2Shape(patch: Record<string, unknown>): Record<string, unknown> {
  if ("backends" in patch || !("baseUrl" in patch || "token" in patch)) return patch;
  const { baseUrl, token, ...rest } = patch;
  const url = typeof baseUrl === "string" ? baseUrl : "http://127.0.0.1:8000";
  let name = "Default";
  try {
    name = new URL(url).host || "Default";
  } catch {
    name = "Default";
  }
  return {
    backends: [{ id: "default", name, baseUrl: url, token: typeof token === "string" ? token : "" }],
    activeBackendId: "default",
    ...rest,
  };
}

/** Persist a settings patch into chrome.storage.local before opening the panel. */
export async function seedSettings(
  context: BrowserContext,
  extensionId: string,
  patch: Record<string, unknown>,
): Promise<void> {
  const page = await context.newPage();
  await page.goto(`chrome-extension://${extensionId}/${PANEL_PATH}`);
  await page.evaluate((p) => chrome.storage.local.set(p), toV2Shape(patch));
  await page.close();
}

/** Open (a fresh instance of) the side panel page and wait for it to render. */
export async function openPanel(context: BrowserContext, extensionId: string): Promise<Page> {
  const page = await context.newPage();
  await page.goto(`chrome-extension://${extensionId}/${PANEL_PATH}`);
  await page.waitForLoadState("domcontentloaded");
  await page.getByRole("heading", { name: "heim" }).first().waitFor({ timeout: 15_000 });
  return page;
}

/**
 * Try to grant the extension host access to `origin`, returning whether it succeeded.
 *
 * The bind mint + session probe run `chrome.scripting.executeScript` in the target tab, which needs
 * host access to that origin. At runtime that access comes from `activeTab` when the user invokes
 * the extension action on the DHIS2 tab; the E2E/docs harness opens the side panel as a page (no
 * action invocation), so it grants the equivalent optional host permission here. This is a
 * test-only stand-in for the runtime `activeTab` grant — it changes nothing about the flow, only
 * the permission plumbing.
 *
 * `chrome.permissions.request` needs a user gesture (hence the real click) and, headless, raises a
 * renderer-blocking prompt that never auto-resolves and can wedge the page's CDP channel. So we
 * race the request against an external deadline and NEVER let it hang the caller: on failure this
 * returns false so the caller can skip the mint-dependent tail or fall back to the mock path.
 */
export async function grantHostPermission(panel: Page, origin: string): Promise<boolean> {
  const pattern = `${origin}/*`;
  if (await panel.evaluate((p) => chrome.permissions.contains({ origins: [p] }), pattern)) return true;
  await panel.evaluate((p) => {
    const b = document.createElement("button");
    b.id = "__heim_grant_host";
    b.textContent = "grant host"; // needs text + size or Playwright's click never finds it actionable
    b.style.cssText = "position:fixed;bottom:0;left:0;z-index:9999;width:120px;height:32px";
    b.addEventListener("click", () => {
      void chrome.permissions.request({ origins: [p] }).then((g) => {
        (window as unknown as { __hostGranted?: boolean }).__hostGranted = g;
      });
    });
    document.body.appendChild(b);
  }, pattern);
  await panel
    .locator("#__heim_grant_host")
    .click({ timeout: 10_000 })
    .catch(() => {});
  // Race the (possibly wedged) request against a hard external deadline so the caller can't hang.
  const granted = await Promise.race([
    panel
      .waitForFunction(() => (window as unknown as { __hostGranted?: boolean }).__hostGranted === true, {
        timeout: 25_000,
      })
      .then(() => true)
      .catch(() => false),
    new Promise<boolean>((r) => setTimeout(() => r(false), 27_000)),
  ]);
  console.log(`[grant-host] host permission for ${origin} granted=${granted}`);
  if (granted)
    await panel.evaluate(() => document.getElementById("__heim_grant_host")?.remove()).catch(() => {});
  return granted;
}

export { expect } from "@playwright/test";
