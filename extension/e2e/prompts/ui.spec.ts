// UI spot-check (the last mile): drive verified prompts through the REAL extension
// side panel, with the page-text toggle on, against a REAL `heim serve` (locked
// gemma). Proves capture -> context block -> /api/chat -> rendered answer.
//
// Host-permission note: the manifest grants page access only for 127.0.0.1 /
// localhost (chrome.scripting can't inject into arbitrary external origins in this
// headless harness). So the content tab is a local 127.0.0.1 page seeded with the
// REAL captured Hacker News text (results/captures.json) - the same bytes the
// capture step recorded from news.ycombinator.com. The capture, context assembly,
// model call, and rendering are all exercised end to end.

import { createServer, type Server } from "node:http";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { AddressInfo } from "node:net";
import { test, expect, openPanel, seedSettings } from "../fixtures";
import { startPromptServer, waitForReady, type PromptServer } from "./promptServer";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const UI_PORT = Number(process.env.HEIM_UI_PORT ?? 4612);

interface Capture {
  key: string;
  url: string;
  title: string;
  pageText: string;
}

function hnCapture(): Capture {
  const caps = JSON.parse(readFileSync(path.join(HERE, "results", "captures.json"), "utf8")) as Capture[];
  const hn = caps.find((c) => c.key === "hn");
  if (!hn) throw new Error("no HN capture; run the harness capture step first");
  return hn;
}

/** Serve one 127.0.0.1 page whose visible text is the captured HN front page. */
function serveContent(cap: Capture): Promise<{ server: Server; url: string }> {
  return new Promise((resolve) => {
    const html = `<!doctype html><html><head><title>${cap.title}</title></head><body><main>${cap.pageText
      .split("\n")
      .map((l) => `<p>${l.replace(/[<>&]/g, "")}</p>`)
      .join("")}</main></body></html>`;
    const server = createServer((_req, res) => {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(html);
    });
    server.listen(0, "127.0.0.1", () => {
      const port = (server.address() as AddressInfo).port;
      resolve({ server, url: `http://127.0.0.1:${port}/` });
    });
  });
}

test.describe.serial("extension UI spot-check against real heim", () => {
  test("JSON extraction + summary + table through the side panel", async ({ context, extensionId }) => {
    // Three sequential real-model asks plus a cold gemma load. The panel (real UI behavior)
    // sends no max_tokens, so each ask is unbounded generation: up to ~8 min per ask
    // (240s first-token poll + 240s stream-finish wait) + ~4 min cold load. Observed 16.6m
    // and 31.5m for identical asks; budget a full hour so teardown never eats the blame.
    test.setTimeout(3_600_000);
    const cap = hnCapture();
    const { server: contentServer, url: contentUrl } = await serveContent(cap);
    let heim: PromptServer | null = null;
    try {
      heim = startPromptServer({ corsOrigin: `chrome-extension://${extensionId}`, port: UI_PORT });
      await waitForReady(heim.baseUrl); // cold gemma load; project timeout covers it

      await seedSettings(context, extensionId, {
        baseUrl: heim.baseUrl,
        token: "",
        pageContextEnabled: true,
        pageTextEnabled: true,
      });
      const panel = await openPanel(context, extensionId);
      const composer = panel.getByPlaceholder(/Message \(Enter to send/);
      await expect(composer).toBeVisible({ timeout: 60_000 });

      // Content tab AFTER the panel so it is the panel's active tab.
      const tab = await context.newPage();
      await tab.goto(contentUrl);
      await tab.bringToFront();

      const assistantText = panel.locator("div.break-words:not(.whitespace-pre-wrap)");

      async function ask(prompt: string, timeout = 240_000): Promise<string> {
        const before = await assistantText.count();
        await composer.fill(prompt);
        await panel.getByRole("button", { name: "Send" }).click();
        let answer = "";
        await expect
          .poll(
            async () => {
              const texts = await assistantText.allInnerTexts();
              answer = texts.length ? texts[texts.length - 1] : "";
              // A new, non-empty assistant bubble that has settled.
              return texts.length > before && answer.trim().length > 0;
            },
            { timeout, intervals: [2000] },
          )
          .toBe(true);
        // Let streaming finish (Send button returns).
        await expect(panel.getByRole("button", { name: "Send" })).toBeVisible({ timeout });
        return (await assistantText.allInnerTexts()).at(-1) ?? "";
      }

      // 1) JSON extraction (the plan's required HN JSON case).
      const jsonOut = await ask(
        "The page text is the Hacker News front page. Extract the first 5 stories as a strict JSON array of objects with keys rank and title. Output ONLY the JSON array.",
      );
      const match = /\[[\s\S]*\]/.exec(jsonOut);
      expect(match, `no JSON array in: ${jsonOut.slice(0, 200)}`).not.toBeNull();
      const parsed = JSON.parse(match![0]) as unknown[];
      expect(Array.isArray(parsed)).toBe(true);
      expect(parsed.length).toBeGreaterThanOrEqual(3);
      console.log(`[ui] JSON extraction: ${parsed.length} items`);

      // 2) Themes summary as bullets.
      const themes = await ask(
        "The page text is the Hacker News front page. Summarize the main themes in exactly 3 bullet points, one sentence each. Output ONLY the bullets.",
      );
      // This tier smokes the last mile (a multi-theme answer reached the panel), not the
      // exact list formatting - that is the API tier's job. The panel renders Markdown, so
      // "- " markers become <li> elements and vanish from innerText; and the model sometimes
      // emits inline "•" bullets on a single line. Count whichever shape arrived.
      const listItems = await panel.locator("div.break-words li").count();
      const themeSegments = themes
        .split(/\n|•/)
        .map((s) => s.trim())
        .filter((s) => s.length > 10).length;
      expect(
        Math.max(listItems, themeSegments),
        `expected a multi-theme answer in: ${themes.slice(0, 200)}`,
      ).toBeGreaterThanOrEqual(2);
      console.log(`[ui] themes summary: ${listItems} list items / ${themeSegments} segments`);

      // 3) Markdown table - rendered as an HTML <table> by the panel's Markdown component,
      // so innerText carries no literal "|"; assert on the rendered rows instead.
      const table = await ask(
        "The page text is the Hacker News front page. List the first 5 stories as a Markdown table with columns Rank | Title. Include header and separator rows. Output ONLY the table.",
      );
      const tableRows = await panel.locator("div.break-words table tr").count();
      expect(
        tableRows > 0 || /\|/.test(table),
        `expected a rendered table or pipes in: ${table.slice(0, 200)}`,
      ).toBe(true);
      console.log(`[ui] table: ${tableRows} rendered rows, raw len=${table.length}`);

      await tab.close();
    } catch (err) {
      if (heim?.logPath) console.log(`[ui] heim serve log tail:\n${heim.tailLog(40)}`);
      throw err;
    } finally {
      // close() alone waits for Chrome's pooled keep-alive sockets to drain - they never do
      // while the persistent context is open, so teardown would hang until the test budget
      // dies (the observed "Tearing down context exceeded the test timeout").
      contentServer.closeAllConnections();
      await new Promise<void>((r) => contentServer.close(() => r()));
      if (heim) await heim.stop();
    }
  });
});
