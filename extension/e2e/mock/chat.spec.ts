// Chat streaming: token/reasoning/tool frames render, markdown renders, aborting
// leaves no error, and an error frame surfaces recoverably.

import { test, expect, openPanel, seedSettings } from "../fixtures";
import { StabburMock, type ChatFrame } from "../mockServer";

const mock = new StabburMock();

test.beforeAll(async () => {
  await mock.start();
});
test.afterAll(async () => {
  await mock.stop();
});
test.beforeEach(() => {
  mock.reset();
  mock.state.phase = "ready";
});

async function openReady(context: import("@playwright/test").BrowserContext, extensionId: string) {
  await seedSettings(context, extensionId, { baseUrl: mock.baseUrl(), token: "" });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  return panel;
}

test("streams tokens, reasoning, tool chips, and renders markdown", async ({ context, extensionId }) => {
  const frames: ChatFrame[] = [
    { type: "token", text: "Hello " },
    { type: "reasoning", text: "resolving org units" },
    { type: "tool", kind: "call", detail: "dhis2__dhis2_cli(list orgunits)" },
    { type: "tool", kind: "result", detail: "1332 organisation units" },
    { type: "token", text: "There are **1332** units." },
    { type: "done" },
  ];
  mock.state.chatFrames = frames;
  mock.state.chatGapMs = 80;

  const panel = await openReady(context, extensionId);
  await panel.getByPlaceholder(/Message \(Enter to send/).fill("how many org units?");
  await panel.getByRole("button", { name: "Send" }).click();

  // Tool chips (targetable by data-testid) appear mid-stream.
  await expect(panel.getByTestId("tool-chip-call")).toContainText("dhis2__dhis2_cli", { timeout: 10_000 });
  await expect(panel.getByTestId("tool-chip-result")).toContainText("1332 organisation units");

  // Markdown bold renders as <strong>.
  await expect(panel.locator("strong", { hasText: "1332" })).toBeVisible({ timeout: 10_000 });
  // The final assistant text is assembled from the streamed tokens.
  await expect(panel.getByText("There are 1332 units.")).toBeVisible();
});

test("a structured tool result collapses to a digest and expands to inlined JSON", async ({
  context,
  extensionId,
}) => {
  // The exFAT bridge envelope: an exit_code wrapper whose stdout is itself JSON. The chip must
  // collapse to a digest (no repr wall) and, when expanded, inline the double-encoded stdout.
  const frames: ChatFrame[] = [
    { type: "tool", kind: "result", detail: '{"exit_code":0,"stdout":"{\\"count\\":1332}"}' },
    { type: "token", text: "done" },
    { type: "done" },
  ];
  mock.state.chatFrames = frames;
  mock.state.chatGapMs = 40;

  // Seed the v2 backends shape directly. Settings are v2-only; seedSettings would translate a flat
  // {baseUrl, token} seed into this same shape, but writing the explicit backends array here keeps
  // the mock's exact URL and is read as-is.
  await seedSettings(context, extensionId, {
    backends: [{ id: "default", name: "Default", baseUrl: mock.baseUrl(), token: "" }],
    activeBackendId: "default",
  });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  await panel.getByPlaceholder(/Message \(Enter to send/).fill("run it");
  await panel.getByRole("button", { name: "Send" }).click();

  // Collapsed: the summary line carries the digest (a top-level key), not a repr wall.
  const chip = panel.getByTestId("tool-chip-result");
  const summary = chip.locator("summary");
  await expect(summary).toContainText("exit_code", { timeout: 10_000 });

  // Expanded: the pre pretty-prints, inlining the double-encoded stdout string as parsed JSON.
  await summary.click();
  const pre = chip.locator("pre");
  await expect(pre).toContainText("count");
  await expect(pre).toContainText("1332");
});

test("aborting a stream ends cleanly with no error banner", async ({ context, extensionId }) => {
  const frames: ChatFrame[] = [];
  for (let i = 0; i < 40; i++) frames.push({ type: "token", text: `chunk${i} ` });
  frames.push({ type: "done" });
  mock.state.chatFrames = frames;
  mock.state.chatGapMs = 300; // ~12s stream; we stop it early

  const panel = await openReady(context, extensionId);
  await panel.getByPlaceholder(/Message \(Enter to send/).fill("stream please");
  await panel.getByRole("button", { name: "Send" }).click();

  const stop = panel.getByRole("button", { name: "Stop" });
  await expect(stop).toBeVisible({ timeout: 10_000 });
  await expect(panel.getByText("chunk0")).toBeVisible(); // some content streamed in
  await stop.click();

  // Streaming ends -> Send button returns; no error banner from an abort.
  await expect(panel.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 10_000 });
  await expect(panel.getByText(/AbortError/)).toHaveCount(0);
});

test("an error frame surfaces a banner and stays recoverable", async ({ context, extensionId }) => {
  mock.state.chatFrames = [
    { type: "token", text: "Partial answer " },
    { type: "error", detail: "runtime exploded" },
    { type: "done" },
  ];
  mock.state.chatGapMs = 40;

  const panel = await openReady(context, extensionId);
  await panel.getByPlaceholder(/Message \(Enter to send/).fill("trigger error");
  await panel.getByRole("button", { name: "Send" }).click();

  await expect(panel.getByText("runtime exploded")).toBeVisible({ timeout: 10_000 });
  // Recoverable: composer is usable again (streaming ended, Send re-enabled).
  await panel.getByPlaceholder(/Message \(Enter to send/).fill("again");
  await expect(panel.getByRole("button", { name: "Send" })).toBeEnabled();
});
