// The browser-executed page-action channel (PAGEACTIONS.md), client half. The mock streams a
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
  headings: { ref: string; name: string; level: number; inferred?: true }[];
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
  truncated: Partial<Record<"headings" | "links" | "buttons" | "fields" | "text", { shown: number; total: number }>>;
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
  title = "Quarterly Report",
): Promise<import("@playwright/test").Page> {
  const tab = await context.newPage();
  await tab.goto(`${mock.baseUrl()}/data-entry`);
  await tab.evaluate(
    ([m, t]) => {
      document.title = t;
      document.body.innerHTML = m;
    },
    [markup, title] as const,
  );
  await tab.bringToFront();
  return tab;
}

/** Drive one page action to completion and hand back what the server was told. */
async function runRead(
  context: import("@playwright/test").BrowserContext,
  extensionId: string,
  markup: string,
  title?: string,
): Promise<(typeof mock.state.pageActionCalls)[number]> {
  mock.state.chatFrames = [
    { type: "page_action", id: "pa-read", action: "page_read", args: {} },
    { type: "token", text: "Read it." },
    { type: "done" },
  ];
  const panel = await openReady(context, extensionId);
  const tab = await openContentTab(context, markup, title);
  await send(panel, "what is on this page?");
  await expect.poll(() => mock.state.pageActionCalls.length, { timeout: 20_000 }).toBe(1);
  await tab.close();
  return mock.state.pageActionCalls[0];
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

  // The prose still rides along — the groups carry names only, so this is the page's ONLY content.
  expect(result.text).toContain("Some readable prose about the reporting period.");
  // Nothing was cut on a page this small, and "nothing was cut" is the empty object: a group that
  // fit is absent, so there is no zero for the model to mistake for a count.
  expect(result.truncated).toEqual({});

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

test("a read that saw nothing fails instead of succeeding empty", async ({ context, extensionId }) => {
  // What a bot check, a consent wall or a half-booted app shell leaves behind: a real document
  // with a title and one sentence, and no structure whatsoever. Reported as a success it is
  // indistinguishable from a page that is genuinely blank, and the model answers "the page is
  // empty" with a successful tool call behind it. So this must not be a success.
  const call = await runRead(context, extensionId, "<p>Enable JavaScript and cookies to continue.</p>", "reuters.com");

  expect(call.ok).toBe(false);
  expect(call.result).toBeUndefined();
  expect(call.error).toContain("saw nothing");
  // The wall's own words ride along — usually the only evidence of WHY — labelled as page content.
  expect(call.error).toContain("Enable JavaScript and cookies to continue.");
  expect(call.error).toContain("untrusted page content");
  // ...and the message refuses to draw the conclusion the model would otherwise draw for itself.
  expect(call.error).toContain("does NOT establish that the page is blank");

  // A read that failed for lack of host access must still be a DIFFERENT message: "I may not look"
  // and "I looked and saw nothing" are different situations with different next moves.
  expect(call.error).not.toContain("no page access");
});

test("one paragraph of prose is a sparse page, not a failed read", async ({ context, extensionId }) => {
  // The other side of the threshold. A page really can be nothing but text, and a read that
  // returned that text has done its job — failing here would turn a working read into an error.
  const prose = `<p>${"A genuinely sparse page that is nothing but prose. ".repeat(6)}</p>`;
  const call = await runRead(context, extensionId, prose, "Notes");

  expect(call.ok).toBe(true);
  const result = call.result as ReadResult;
  expect(result.text).toContain("nothing but prose");
  expect(result.headings).toEqual([]);
});

/** A table-era front page: titles in `<td><span><a>`, with no `<h*>` and no role="heading" —
 *  plus the two things that must NOT be promoted to headings alongside them. */
const TABLE_MARKUP = `
  <table>
    <tr><td class="title"><span class="titleline">
      <a href="https://example.com/a">Decompiling a Nintendo 64 game in eighty four days</a>
      <span class="sitebit"> (example.com)</span>
    </span></td></tr>
    <tr><td class="subtext"><a href="https://example.com/c1">42 comments</a></td></tr>
    <tr><td class="title"><span class="titleline">
      <a href="https://example.com/b">Saving a hundred terabytes of memory by optimizing a cache</a>
      <span class="sitebit"> (example.com)</span>
    </span></td></tr>
    <tr><td><a href="https://example.com/login">login</a></td></tr>
  </table>
  <p>A paragraph of ordinary running prose that happens to contain
     <a href="https://example.com/full">a link to the full article here</a>
     somewhere in the middle of the sentence it belongs to.</p>
  <nav><a href="https://example.com/nav">Browse every past submission by date</a></nav>
`;

test("a page that declares no outline gets one inferred, and says so", async ({ context, extensionId }) => {
  const call = await runRead(context, extensionId, TABLE_MARKUP, "Table News");
  expect(call.ok).toBe(true);
  const result = call.result as ReadResult;

  // The titles are recovered — without this the whole front page is undifferentiated links and
  // the model cannot tell a headline from "login".
  const names = result.headings.map((h) => h.name);
  expect(names).toContain("Decompiling a Nintendo 64 game in eighty four days");
  expect(names).toContain("Saving a hundred terabytes of memory by optimizing a cache");

  // And every one of them is marked as a guess, so an inferred outline can never be read as a
  // page's own markup.
  for (const h of result.headings) expect(h.inferred).toBe(true);

  // A ref the read already handed back, so the pair addresses the same element: the inferred
  // heading does not invent a handle, it re-labels a link the model was given anyway.
  const first = result.headings[0];
  expect(result.links.some((l) => l.ref === first.ref)).toBe(true);

  // THE PART THAT MATTERS MORE THAN THE RECALL. Mislabelling navigation or a mid-sentence
  // reference as a headline is worse than having no outline, so each guard gets its own check:
  // an inline prose link is a small fraction of its paragraph's text...
  expect(names).not.toContain("a link to the full article here");
  // ...a link inside a landmark is navigation however long its label is...
  expect(names).not.toContain("Browse every past submission by date");
  // ...and a short label is not a title.
  expect(names).not.toContain("login");
  expect(names).not.toContain("42 comments");
});

test("a page with real headings is never second-guessed", async ({ context, extensionId }) => {
  // Same markup, one real heading added. The fallback is a fallback: a document that declared an
  // outline gets exactly that outline, even where a guess would have found more.
  const call = await runRead(context, extensionId, `<h1>Front page</h1>${TABLE_MARKUP}`, "Table News");
  expect(call.ok).toBe(true);
  const result = call.result as ReadResult;

  expect(result.headings).toEqual([expect.objectContaining({ name: "Front page", level: 1 })]);
  for (const h of result.headings) expect(h.inferred).toBeUndefined();
});

test("truncation says how much was cut, not merely that it was", async ({ context, extensionId }) => {
  // 200 distinct links against a cap of 150, plus 30 repeats of one of them. A model told only
  // "the links were trimmed" cannot tell a couple of lost entries from a page it is seeing a
  // fraction of, and the second is when it must stop trusting the list.
  const distinct = Array.from(
    { length: 200 },
    (_, i) => `<a href="https://example.com/p${i}">Destination number ${i}</a>`,
  ).join("");
  const repeats = `<a href="https://example.com/p0">Destination number 0</a>`.repeat(30);
  const call = await runRead(context, extensionId, `<h1>Index</h1>${distinct}${repeats}`, "Index");
  expect(call.ok).toBe(true);
  const result = call.result as ReadResult;

  // Shown out of held — the ratio is the signal, and 30 repeats of one link are not 30 links the
  // model is missing, so they are counted as neither shown nor total.
  expect(result.truncated.links).toEqual({ shown: 150, total: 200 });
  expect(result.links).toHaveLength(150);

  // A link listed once, however many times the page repeats it: same words, same destination, so
  // a ref to either does the identical thing and the extra entries buy the model nothing.
  expect(result.links.filter((l) => l.name === "Destination number 0")).toHaveLength(1);

  // Groups that fit are absent rather than zero, so `truncated` costs nothing to report when
  // there is nothing to report.
  expect(result.truncated.headings).toBeUndefined();
  expect(result.truncated.buttons).toBeUndefined();
  expect(result.truncated.fields).toBeUndefined();
});

test("a tab outside the bound target is refused", async ({ context, extensionId }) => {
  // A registry whose only target lives on the target-site origin, while the user is looking at a
  // page on a different one. PAGEACTIONS.md rule 3: the bound/matched tab only.
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
