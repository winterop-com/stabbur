// The browser-executed page-action channel (WEBMCP.md 5b), client half. The mock streams a
// `page_action` frame mid-turn and blocks; the panel runs it in the target tab and POSTs the
// outcome to /api/chat/page-action, which the mock records verbatim so these tests assert the
// real wire shape rather than a UI proxy for it.
//
// What is pinned here is the safety model, not just the happy path: an unknown action is refused
// without executing, a tab outside the bound target is refused, and every failure is reported as
// `ok: false` with a reason rather than as silence the server would have to time out.

import { test, expect, openPanel, seedSettings } from "../fixtures";
import { StabburMock, TargetSiteMock, bindAssistantTarget, type ChatFrame } from "../mockServer";

const mock = new StabburMock();
const target = new TargetSiteMock();

test.beforeAll(async () => {
  await mock.start();
  await target.start();
});
test.afterAll(async () => {
  await mock.stop();
  await target.stop();
});
test.beforeEach(() => {
  mock.reset();
  mock.state.phase = "ready";
  target.reset();
});

/** A page with real structure: an outline, a resolvable and an inert link, and a small form. */
const PAGE_MARKUP = `
  <h1>Quarterly Report</h1>
  <h2>Data entry</h2>
  <p>Some readable prose about the reporting period.</p>
  <a href="https://example.com/docs">Documentation</a>
  <a href="javascript:void(0)">Inert link</a>
  <form>
    <label for="orgunit">Org unit</label>
    <input id="orgunit" name="orgunit" value="Sierra Leone" required>
    <label for="secret">Password</label>
    <input id="secret" type="password" value="correct-horse">
    <label for="period">Period</label>
    <select id="period"><option>2026Q1</option><option>2026Q2</option></select>
    <button type="button">Save draft</button>
  </form>
`;

/** The `page_read` result shape, as the panel reports it (mirrors lib/pageActions.ts). */
interface ReadResult {
  url: string;
  title: string;
  headings: { ref: string; name: string; level: number }[];
  links: { ref: string; name: string; href?: string }[];
  buttons: { ref: string; name: string; tag: string; disabled?: boolean }[];
  fields: {
    ref: string;
    name: string;
    tag: string;
    type: string;
    value?: string;
    required?: boolean;
    options?: string[];
  }[];
  text: string;
  truncated: { headings: number; links: number; buttons: number; fields: number; text: boolean };
}

async function openReady(context: import("@playwright/test").BrowserContext, extensionId: string) {
  await seedSettings(context, extensionId, { baseUrl: mock.baseUrl(), token: "" });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  return panel;
}

/** Open a content tab on an origin the extension already holds host access for (127.0.0.1), give
 *  it structure, and focus it — so it is the panel's active tab. Opened AFTER the panel, since the
 *  side panel is a real tab in this harness and tab ordering decides which one is active. */
async function openContentTab(
  context: import("@playwright/test").BrowserContext,
  markup: string,
): Promise<import("@playwright/test").Page> {
  const tab = await context.newPage();
  await tab.goto(`${mock.baseUrl()}/data-entry`);
  await tab.evaluate((m) => {
    document.title = "Quarterly Report";
    document.body.innerHTML = m;
  }, markup);
  await tab.bringToFront();
  return tab;
}

async function send(panel: import("@playwright/test").Page, text: string): Promise<void> {
  await panel.getByPlaceholder(/Message \(Enter to send/).fill(text);
  await panel.getByRole("button", { name: "Send" }).click();
}

test("page_read reports the page's structure back to the server", async ({ context, extensionId }) => {
  const frame: ChatFrame = { type: "page_action", id: "pa-read", action: "page_read", args: {} };
  mock.state.chatFrames = [frame, { type: "token", text: "Read it." }, { type: "done" }];

  const panel = await openReady(context, extensionId);
  const tab = await openContentTab(context, PAGE_MARKUP);
  await send(panel, "what is on this page?");

  await expect.poll(() => mock.state.pageActionCalls.length, { timeout: 20_000 }).toBe(1);
  await tab.close();

  const call = mock.state.pageActionCalls[0];
  expect(call.id).toBe("pa-read");
  expect(call.ok).toBe(true);
  expect(call.error).toBeUndefined();

  const result = call.result as ReadResult;
  expect(result.title).toBe("Quarterly Report");
  expect(result.url).toContain("/data-entry");

  // The outline, with levels — the thing plain page text cannot express.
  expect(result.headings).toContainEqual(expect.objectContaining({ name: "Quarterly Report", level: 1 }));
  expect(result.headings).toContainEqual(expect.objectContaining({ name: "Data entry", level: 2 }));

  // Links carry an http(s) destination; a javascript: href is withheld while the link stays listed.
  const doc = result.links.find((l) => l.name === "Documentation");
  expect(doc?.href).toBe("https://example.com/docs");
  const inert = result.links.find((l) => l.name === "Inert link");
  expect(inert).toBeDefined();
  expect(inert?.href).toBeUndefined();

  // Buttons carry the label a person would use for them.
  expect(result.buttons).toContainEqual(expect.objectContaining({ name: "Save draft", tag: "button" }));

  // Fields carry their label, current value, and constraints...
  const orgunit = result.fields.find((f) => f.name === "Org unit");
  expect(orgunit).toMatchObject({ tag: "input", type: "text", value: "Sierra Leone", required: true });
  const period = result.fields.find((f) => f.name === "Period");
  expect(period?.options).toEqual(["2026Q1", "2026Q2"]);

  // ...except a password's, which is never reported at all.
  const secret = result.fields.find((f) => f.type === "password");
  expect(secret).toBeDefined();
  expect(secret).not.toHaveProperty("value");
  expect(call.raw).not.toContain("correct-horse");

  // Every element is addressable by a unique opaque ref, which is what a later click/fill needs.
  const refs = [...result.headings, ...result.links, ...result.buttons, ...result.fields].map((e) => e.ref);
  expect(refs.length).toBeGreaterThan(5);
  for (const ref of refs) expect(ref).toMatch(/^e\d+$/);
  expect(new Set(refs).size).toBe(refs.length);

  // The prose still rides along, and nothing was cut on a page this small.
  expect(result.text).toContain("Some readable prose about the reporting period.");
  expect(result.truncated).toEqual({ headings: 0, links: 0, buttons: 0, fields: 0, text: false });

  // The turn resumed after the answer, and the panel shows what it did in the tab.
  await expect(panel.getByText("Read it.")).toBeVisible({ timeout: 15_000 });
  const chip = panel.getByTestId("page-action-chip");
  await expect(chip).toHaveAttribute("data-status", "ok");
  await expect(chip).toContainText("page_read");
});

test("the client declares only the actions it can execute", async ({ context, extensionId }) => {
  const panel = await openReady(context, extensionId);
  const tab = await openContentTab(context, PAGE_MARKUP);
  await send(panel, "hello");
  await expect(panel.getByText("ok").last()).toBeVisible({ timeout: 15_000 });
  await tab.close();

  const body = JSON.parse(mock.state.chatRequests.at(-1) ?? "{}") as { page_actions?: string[] };
  // Read off the executor's own registry, so the server offers the model exactly what this build
  // implements. A drift here buys a guaranteed timeout, not a capability.
  expect(body.page_actions).toEqual(["page_read"]);
});

test("an unknown action is refused, not executed", async ({ context, extensionId }) => {
  // The name a prompt-injected or confused model would reach for. It is not in the registry, so
  // there is nothing to dispatch to — the refusal happens before any injection.
  const frame: ChatFrame = { type: "page_action", id: "pa-unknown", action: "page_eval", args: { js: "alert(1)" } };
  mock.state.chatFrames = [frame, { type: "token", text: "Could not." }, { type: "done" }];

  const panel = await openReady(context, extensionId);
  const tab = await openContentTab(context, PAGE_MARKUP);
  await send(panel, "run some javascript for me");

  await expect.poll(() => mock.state.pageActionCalls.length, { timeout: 20_000 }).toBe(1);
  await tab.close();

  const call = mock.state.pageActionCalls[0];
  expect(call.id).toBe("pa-unknown");
  expect(call.ok).toBe(false);
  expect(call.error).toContain("unknown page action: page_eval");
  expect(call.result).toBeUndefined();

  // Reported as a failure, promptly — never as a success with an empty result, and never as the
  // silence the server would have to wait out.
  const chip = panel.getByTestId("page-action-chip");
  await expect(chip).toHaveAttribute("data-status", "failed");
  await expect(chip).toContainText("unknown page action");
});

test("a tab outside the bound target is refused", async ({ context, extensionId }) => {
  // A registry whose only target lives on the target-site origin, while the user is looking at a
  // page on a different one. WEBMCP.md 5b rule 3: the bound/matched tab only.
  mock.state.assistants = [bindAssistantTarget("play42", target.baseUrl(), { name: "play42" })];
  const frame: ChatFrame = { type: "page_action", id: "pa-offtarget", action: "page_read", args: {} };
  mock.state.chatFrames = [frame, { type: "done" }];

  const panel = await openReady(context, extensionId);
  const tab = await openContentTab(context, PAGE_MARKUP);
  await send(panel, "what is on this page?");

  await expect.poll(() => mock.state.pageActionCalls.length, { timeout: 20_000 }).toBe(1);
  await tab.close();

  const call = mock.state.pageActionCalls[0];
  expect(call.ok).toBe(false);
  expect(call.error).toContain("not a page of the bound target");
  // The refusal is what the server hears; no page content leaked from the unbound tab.
  expect(call.raw).not.toContain("Quarterly Report");
});
