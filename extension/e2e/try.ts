// Interactive test drive: launch a HEADED Chromium with the built extension loaded,
// start a real `kodo serve` (gemma + dhis2 bridge -> play42, read-only), seed the
// panel settings, and leave everything running until Ctrl+C.
//
//   bun run e2e/try.ts
//
// Reuses the live-E2E fixture (startLiveServer) so the backend is exactly what the
// live tier verifies: locked gemma-4-12B, [assistant] play42 block, published bridge.

import path from "node:path";
import { fileURLToPath } from "node:url";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { chromium } from "@playwright/test";
import { startLiveServer, warmBridge, LIVE_PORT } from "./live/liveServer";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const EXTENSION_PATH = path.resolve(HERE, "..", ".output", "chrome-mv3");

async function main(): Promise<void> {
  console.log("[try] launching headed Chromium with the extension ...");
  const userDataDir = mkdtempSync(path.join(tmpdir(), "kodo-ext-try-"));
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

  console.log("[try] starting kodo serve (gemma-4-12B, play42, read-only) ...");
  const server = startLiveServer(extensionId);
  process.on("SIGINT", () => {
    void (async () => {
      console.log("\n[try] shutting down kodo serve + browser ...");
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
  console.log("[try]   real side panel: click the kodo icon in the toolbar (puzzle-piece menu)");
  console.log(`[try]   backend:     http://127.0.0.1:${LIVE_PORT} (gemma-4-12B locked, dhis2 bridge -> play42)`);
  console.log("[try]   page-context + page-text toggles are ON; an HN tab is open for prompt-catalog testing");
  console.log("[try] Ctrl+C here stops kodo serve and closes the browser.");

  await new Promise(() => {}); // run until Ctrl+C
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
