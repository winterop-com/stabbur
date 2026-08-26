// Interactive test drive: launch a HEADED Chromium with the built extension loaded,
// start a real `stabbur serve` (gemma + dhis2 bridge -> play42, read-only), seed the
// panel settings, and leave everything running until Ctrl+C.
//
// This is the engine behind `stabbur ext-dev` (the supported launcher, which owns discovery,
// preconditions, the build, and process lifecycle). Still runnable directly:
//
//   bun run e2e/try.ts
//
// Env contract (both set by `stabbur ext-dev`; unset -> today's byte-identical behavior):
//   STABBUR_EXT_DEV_MULTI=1        -> load the two-target fixture (play42 + play41) instead of play42
//   STABBUR_EXT_DEV_FLAVOR=dhis2   -> load `.output/chrome-mv3-dhis2` instead of `.output/chrome-mv3`
//
// Reuses the live-E2E fixture (startLiveServer) so the backend is exactly what the
// live tier verifies: locked model, [assistant]/[[assistants]] block(s), published bridge.

import path from "node:path";
import { fileURLToPath } from "node:url";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { chromium } from "@playwright/test";
import { startLiveServer, warmBridge, LIVE_PORT, MULTI_TARGETS, LIVE_MULTI_MODEL, MULTI_SYSTEM_PROMPT } from "./live/liveServer";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const MULTI = process.env.STABBUR_EXT_DEV_MULTI === "1";
const OUT_SUFFIX = process.env.STABBUR_EXT_DEV_FLAVOR === "dhis2" ? "-dhis2" : "";
const EXTENSION_PATH = path.resolve(HERE, "..", ".output", `chrome-mv3${OUT_SUFFIX}`);

async function main(): Promise<void> {
  console.log("[try] launching headed Chromium with the extension ...");
  const userDataDir = mkdtempSync(path.join(tmpdir(), "stabbur-ext-try-"));
  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: "chromium",
    headless: false,
    viewport: null,
    args: [
      `--disable-extensions-except=${EXTENSION_PATH}`,
      `--load-extension=${EXTENSION_PATH}`,
      "--no-first-run",
      "--no-default-browser-check",
    ],
  });

  let worker = context.serviceWorkers()[0];
  if (!worker) worker = await context.waitForEvent("serviceworker", { timeout: 30_000 });
  const extensionId = /chrome-extension:\/\/([a-z]+)\//.exec(worker.url())?.[1];
  if (!extensionId) throw new Error("could not resolve extension id");
  console.log(`[try] extension id: ${extensionId}`);

  console.log("[try] warming the dhis2 bridge (uvx cache) ...");
  warmBridge();

  console.log(
    MULTI
      ? "[try] starting stabbur serve (Ornith-1.0-9B, play42 + play41 multi-target, read-only) ..."
      : "[try] starting stabbur serve (gemma-4-12B, play42, read-only) ...",
  );
  const server = MULTI
    ? startLiveServer(extensionId, {
        targets: MULTI_TARGETS,
        model: LIVE_MULTI_MODEL,
        systemPrompt: MULTI_SYSTEM_PROMPT,
      })
    : startLiveServer(extensionId);
  process.on("SIGINT", () => {
    void (async () => {
      console.log("\n[try] shutting down stabbur serve + browser ...");
      await server.stop();
      await context.close().catch(() => {});
      process.exit(0);
    })();
  });

  // Seed settings while the model loads, then open the panel page.
  const seed = await context.newPage();
  await seed.goto(`chrome-extension://${extensionId}/sidepanel.html`);
  // The extension is v2-only (no legacy flat migration), so seed the v2 backends shape directly.
  await seed.evaluate(
    (baseUrl) =>
      chrome.storage.local.set({
        backends: [{ id: "default", name: new URL(baseUrl).host, baseUrl, token: "" }],
        activeBackendId: "default",
        pageContextEnabled: true,
        pageTextEnabled: true,
      }),
    `http://127.0.0.1:${LIVE_PORT}`,
  );
  await seed.close();

  const panel = await context.newPage();
  await panel.goto(`chrome-extension://${extensionId}/sidepanel.html`);
  const hn = await context.newPage();
  await hn.goto("https://news.ycombinator.com/");
  await panel.bringToFront();

  console.log(`[try] waiting for the model to load (cold start can take minutes) ...`);
  const deadline = Date.now() + 600_000;
  for (;;) {
    try {
      const res = await fetch(`http://127.0.0.1:${LIVE_PORT}/api/status`);
      if (res.ok && ((await res.json()) as { state: string }).state === "ready") break;
    } catch {
      /* not up yet */
    }
    if (Date.now() > deadline) {
      console.log(`[try] server not ready after 600s; log tail:\n${server.tailLog(30)}`);
      break;
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  console.log("[try] READY.");
  console.log(`[try]   panel tab:   chrome-extension://${extensionId}/sidepanel.html`);
  console.log("[try]   real side panel: click the stabbur icon in the toolbar (puzzle-piece menu)");
  console.log(
    MULTI
      ? `[try]   backend:     http://127.0.0.1:${LIVE_PORT} (Ornith-1.0-9B locked, dhis2 bridges -> play42 + play41)`
      : `[try]   backend:     http://127.0.0.1:${LIVE_PORT} (gemma-4-12B locked, dhis2 bridge -> play42)`,
  );
  console.log("[try]   page-context + page-text toggles are ON; an HN tab is open for prompt-catalog testing");
  console.log("[try] Ctrl+C here stops stabbur serve and closes the browser.");

  await new Promise(() => {}); // run until Ctrl+C
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
