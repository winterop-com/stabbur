# WebMCP — research and maturity assessment for stabbur

## Correction (2026-08-26): "WebMCP" names at least two different things

The assessment below is about the **browser API** (`document.modelContext`, W3C WebML CG,
Chrome origin trial). That is not the only thing shipping under the name, and conflating them
makes claims like "vendor X supports WebMCP today" sound bigger than they are:

1. **The browser API** — `document.modelContext.registerTool()`. Needs a browser (or an
   extension polyfill). Chrome 149 origin trial / `chrome://flags/#enable-webmcp-testing`;
   nothing shipped on by default anywhere. Everything below applies to this.
2. **Site-embedded JS libraries that expose an MCP server** — e.g.
   [webmcp.dev](https://webmcp.dev/) (`@jason.today/webmcp`): a site adds a `<script>`, and an
   ordinary MCP client (Claude Desktop, or anything speaking MCP) connects. No browser support
   involved at all. `@mcp-b/webmcp-local-relay` is the same shape.
3. **Edge injection** — [Cloudflare's developer preview](https://blog.cloudflare.com/webmcp/)
   (2026-08-06) injects a bridge script at the CDN so the *origin* needs no code change. Still
   opt-in per domain by its owner, still a preview.

**Why this matters for stabbur:** an MCP client does not need to "support WebMCP" for flavor 2 —
it is just an MCP server, so stabbur could consume one **today with no code change** (an entry in
`.mcp.json`). So vendor announcements of support are largely a non-event for us; stabbur is
already an MCP client. The binding constraint is unchanged and is on the *other* side: some
site has to expose tools, and for our purposes that site is DHIS2, which exposes none. The
browser API only becomes interesting if it is what drives sites to do that.



Research date: **2026-08-26**. All dates below are the dates of the source, not of this document.
Primary sources (spec repo, standards-position issues, Chrome/Cloudflare blogs, GitHub API) were
preferred; the SEO/AI-blog layer around WebMCP is large, contradictory, and demonstrably wrong on
several checkable facts, so it is quoted only where flagged.

---

## 1. What WebMCP actually is

**A browser API that lets a *web page* register callable tools for an AI agent.** It is not a
transport, not a wire protocol, and — despite the name — not an MCP binding.

The page registers tools imperatively:

```js
await document.modelContext.registerTool({
  name: "add-todo",
  description: "Add a new item to the user's active todo list",
  inputSchema: { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
  async execute({ text }) {
    await addTodoItemToCollection(text);
    return { content: [{ type: "text", text: `Added todo item: "${text}"` }] };
  }
}, { signal: controller.signal });
```

A consumer (agent) side was added much later:

```js
const tools = await document.modelContext.getTools();          // + { fromOrigins: [...] }
const result = await document.modelContext.executeTool(tool, { text: "Buy groceries" },
                                                       { signal });
document.modelContext.addEventListener("toolchange", ...);      // dynamic re-registration
```

There is also a **declarative API** — annotations on plain `<form>` elements that the browser
synthesizes into tools (separate explainer, `declarative-api-explainer.md`; its schema-synthesis
algorithm is still a TODO in the spec).

Source: [README.md, webmachinelearning/webmcp](https://github.com/webmachinelearning/webmcp) —
first published **2025-08-13**, last commit **2026-08-26**.

### Scoping / permissions in the API
- Gated by a **`tools` Permissions Policy**, default `self`. Cross-origin iframes need
  `allow="tools"`. `Permissions-Policy: tools=()` disables it.
- Requires origin-isolated documents; disabled if `document.domain` is set.
- By default a tool is exposed only to **the registering document, same-origin documents in the
  same frame tree, and built-in browser agents**. `exposedTo: ["https://partner.example"]` opts
  additional origins in; `getTools({ fromOrigins: [...] })` opts them in on the reading side. Both
  sides must agree.

### How it differs from ordinary MCP
| | MCP (stdio/HTTP) | WebMCP |
|---|---|---|
| Where tools live | a server process / remote endpoint | inside the page's JS realm |
| Auth | you replicate the user's credentials server-side | the page's existing logged-in session, implicitly |
| Lifetime | as long as the server runs | the document's lifetime (dies on navigation; suspended in BFCache) |
| Wire format | JSON-RPC, MCP spec | **unspecified** — the spec says browsers may expose tools "via Model Context Protocol, other proprietary 'function calling' methods, or any other way" |
| Who is the client | your agent | the browser's built-in agent (primarily), or an in-page/iframe agent |

The explainer's own framing: a WebMCP page is "an in-page MCP server that implements tools exposing
client-side logic and DOM interaction rather than server-side APIs." The README's *Alternatives
Considered* section explicitly rejects adopting MCP itself in the browser, because MCP "lacks native
web concepts like origins, standard browser permissions, DOM integration, and tab-level lifecycle."

### Who is behind it
Editors listed on the spec (as of 2026-08-26): **Brandon Walderman (Microsoft), Khushal Sagar
(Google), Dominic Farolino (Google)**. Chrome feature owners are all Google. Anthropic — the author
of MCP itself — has no visible involvement in the repo despite the name.

---

## 2. Standardization status

**Stage: incubation in a W3C Community Group. Not on the W3C Recommendation track.**

- Venue: **W3C Web Machine Learning Community Group** (a CG, not a WG). Document type on the spec
  page: *Draft Community Group Report*, dated 2026-08-26.
  [spec](https://webmachinelearning.github.io/webmcp/)
- Chrome Platform Status records maturity as `"Specification being incubated in a Community Group"`
  and Chrome status `"Proposed"`.
  [chromestatus 5117755740913664](https://chromestatus.com/feature/5117755740913664)
- Repo activity is genuinely high: 3,144 stars, 109 open issues, commits landing daily. Last commit
  **2026-08-26** (checked via GitHub API).

### The spec is being written *right now*, consumer-side first
This matters more than the star count. Commit history on `index.bs`:

| Date | Change |
|---|---|
| 2026-05-27 | issue #173 closed — **tools moved from Window-scoped to Document-scoped**, i.e. `navigator.modelContext` → `document.modelContext` |
| 2026-07-21 | "Spec the `getTools()` API" (#223) |
| 2026-08-14 | "Spec the `executeTool()` API" (#226); `inputSchema` type changed `DOMString` → `object` (#241) |
| 2026-08-17 | "Accept an object in `executeTool()` instead of a JSON string" (#246) |
| 2026-08-19 | `AbortSignal` integration specced (#247); in-flight-execution semantics (#248) |
| 2026-08-20 | open issue #251: change `executeTool()` input type from `object` to `any` |

**The entire agent-consumption half of the API — the half stabbur would use — was formally specified in
the last six weeks and had a breaking signature change nine days ago.** The namespace itself moved
three months ago.

Open issues that are structural, not cosmetic:
- [#236](https://github.com/webmachinelearning/webmcp/issues/236) "Reposition feature as a generic
  communication tool?" — Mozilla's Jake Archibald arguing the primitive should be cross-realm
  function calling, not an agent API. Farolino (2026-08-24) said they are "supportive of most" of it.
- [#227](https://github.com/webmachinelearning/webmcp/issues/227) tool discovery beyond a single
  traversable navigable — open.
- [#74](https://github.com/webmachinelearning/webmcp/issues/74) WebExtensions API — open (below).
- Security/consent hook, cross-origin analysis, declarative schema synthesis — all **TODO** in the
  spec text.

### Other-vendor positions (this is the most important evidence)

| Vendor | Position | Date |
|---|---|---|
| **Apple / WebKit** | **`position: oppose`** | filed 2026-05-28, position stated 2026-06-03, resolved 2026-06-11 |
| **Mozilla** | **`position: neutral`** | filed 2026-05-28, resolved 2026-08-05 |
| **Google, Microsoft** | authors / implementing | — |
| **Brave** | experimental support in Leo AI chat | [brave-browser#55232](https://github.com/brave/brave-browser/issues/55232) |

[WebKit standards-positions #670](https://github.com/WebKit/standards-positions/issues/670) — labels
include `concerns: security`, `privacy`, `API design`, `duplication`, `portability`, `venue`,
`use cases`, `internationalization`. Marcos Cáceres / Mike Wyrzykowski's objections, condensed:

- A parallel agent-facing layer is the wrong fix; the gap belongs in HTML/ARIA "where the user,
  assistive technology, and agents all benefit."
- It makes "an agent is driving" an **observable fact**, so nothing keeps the agent surface and the
  human surface in parity — a site can give agents capabilities it withholds from humans, or vice
  versa (the "screen-reader-blocking problem, but applied to AI agents").
- No consent or reversibility model for consequential actions; `readOnlyHint` is advisory,
  `toolautosubmit` submits without review.
- "Despite the name, the spec does not prescribe the format in which tools are exposed... this is not
  really an MCP binding."
- The Machine Learning CG is the **wrong venue**; this is HTML/ARIA work (WHATWG, ARIA WG, APA WG).

Cáceres, 2026-06-17, declining to answer Farolino's follow-ups point by point: *"WebMCP proposes a
new solution before the actual problem has been established."*

[Mozilla standards-positions #1412](https://github.com/mozilla/standards-positions/issues/1412) —
Ben VanderSloot, 2026-06-01: valuable abstraction, but sites can "tar pit automated browsers, provide
prompt injection that is invisible to typical users, collect user data from the inputs of the tools";
the name is misleading because *"There is no MCP here"*; and — directly relevant to stabbur — *"As of
now, use of this mechanism is restricted to browser developer's own products and in-page agents. An
important step to ensuring an open ecosystem of web browsing agents is opening up tool invocation to
other consumers, by adding WebExtension and WebDriver BiDi integrations."* Marked neutral,
revisitable "once there is more evidence of how it will be used by sites."

By 2026-08-24/25 Mozilla engineers (Archibald, Thomson) were engaging constructively on the
**imperative** API specifically; the formal position is still `neutral` and the vendor split is
**2 for / 1 neutral / 1 opposed**.

Note: Chrome's own Intent to Experiment (2026-05-15) recorded Gecko and WebKit as "no signals" —
that was two weeks before either position was filed and is now stale, as is the chromestatus entry,
which still reads "No signal" for both. Do not trust chromestatus's signal fields here.

---

## 3. Browser support — announced vs shipping

**No browser ships WebMCP on stable to the open web. Nothing is on by default anywhere.**

| Browser | Reality | Date |
|---|---|---|
| **Chrome** | **Origin trial**, M149–M156, desktop + Android + WebView. Requires a per-origin token; the site opts in. Local dev via `chrome://flags/#enable-webmcp-testing`. | [Intent to Experiment 2026-05-15](https://groups.google.com/a/chromium.org/g/blink-dev/c/gmYffo5WOE8/m/OJxuQRP3AAAJ); [OT blog 2026-06-09](https://developer.chrome.com/blog/ai-webmcp-origin-trial) |
| **Chrome (earlier)** | Behind a flag from ~M146 (Feb 2026); partial and not spec-compliant on `registerTool()` at that point | see §4 |
| **Edge** | **Origin trial** live in Edge 150 (Chromium, same implementation) | [Edge OT registration](https://developer.microsoft.com/en-us/microsoft-edge/origin-trials/trials/0b76fe60-b266-458e-a285-04e375c0c31a) |
| **Firefox** | No implementation. [bug 2018306](https://bugzilla.mozilla.org/show_bug.cgi?id=2018306) is a tracking bug, not work in progress | — |
| **Safari** | No implementation, position is oppose | — |
| **Brave** | Experimental, wired to Leo AI chat only | — |

Source of truth:
[implementation-status.md](https://github.com/webmachinelearning/webmcp/blob/main/implementation-status.md)
(added 2026-08-12).

**An origin trial is a time-boxed experiment that can be cancelled.** M156 is the end date on the
current registration; nothing has been scheduled to ship. There is no "Intent to Ship" on blink-dev.

### The consumer side is thinner than the producer side
Google I/O 2026 (2026-05-21, [Chrome at I/O
recap](https://developer.chrome.com/blog/chrome-at-io26)) says only: *"Gemini in Chrome **will soon**
support WebMCP APIs."* As of 2026-08-26 I found no announcement that it has shipped. So today,
in Chrome, sites can register tools in an origin trial and **the browser's own agent does not
reliably call them**.

Corroborating field data — [issue
#256](https://github.com/webmachinelearning/webmcp/issues/256), an origin-trial research note filed
2026-08-22 by an outside experimenter running 100 trial slots across Codex, ChatGPT/Chrome,
Antigravity and Edge: *"Native discovery was irregular. Antigravity produced two server-confirmed
invocations in five attempts, only one of which produced a usable resumption report. The observed
ChatGPT/Chrome and Edge configurations produced no confirmed native invocation."*

---

## 4. Ecosystem adoption

### Site-side: genuinely near zero, with a recent CDN-shaped wrinkle

- **2026-05-28**, [freeCodeCamp, "A Developer's Guide to WebMCP: Shipping a 0% Adoption
  Standard"](https://www.freecodecamp.org/news/a-developers-guide-to-webmcp/): the author scanned
  **111,076 top domains and found zero** shipping WebMCP — the only standard at 0% among 17
  AI-infrastructure standards measured. He had shipped it on two of his own sites (chudi.dev,
  citability.dev) since February 2026 and logged **zero agent calls** in the five days after launch.
  This predates the origin trial, but it is the only real measurement I found.
- **Named "experimenting" partners** (Google I/O, 2026-05-21): Expedia, Booking.com, Shopify, Credit
  Karma, TurboTax, Redfin, Etsy, Instacart, Target. "Experimenting", not shipped — and a Google
  slide, not an independent observation.
- **Cloudflare**, [2026-08-06](https://blog.cloudflare.com/webmcp/): a **developer preview** that
  injects a `bridge.js` at the edge so a Cloudflare-fronted site can expose pre-built "tool packs"
  with no origin changes. **Opt-in** via dashboard (Agent Readiness > WebMCP); two packs; described
  by InfoQ ([2026-08-10](https://www.infoq.com/news/2026/08/cloudflare-webmcp/)) as "highly work in
  progress". This is a real distribution lever if it graduates — it is not GA and not default-on.
- **Unverified**: several blogs claim Shopify made WebMCP "default-on for every Liquid storefront"
  on 2026-08-05 and that WebMCP is now "default-on across a large share of commerce". I could not
  find a Shopify or Cloudflare primary source for that, and the same posts also stated flatly wrong
  facts elsewhere (e.g. that Mozilla and Apple are co-developing the spec — Apple formally opposed
  it). **Treat as rumour.**

### Tooling that does exist and is maintained

| Thing | What it is | Activity |
|---|---|---|
| [`@mcp-b/*` npm packages](https://github.com/WebMCP-org/npm-packages) | The descendant of MCP-B, the Jan-2025 prototype WebMCP grew out of. Packages: `global` (runtime), `webmcp-polyfill`, `webmcp-extension`, **`webmcp-local-relay`**, `transports`, `react-webmcp`, `webmcp-ts-sdk`, `webmcp-types` | last push **2026-08-25** |
| [`GoogleChromeLabs/webmcp-tools`](https://github.com/GoogleChromeLabs/webmcp-tools) | Google's demos + dev utilities, incl. the "Model Context Tool Inspector" extension and a polyfill | 495★, last push 2026-08-19 |
| [`igrigorik/AgentBoard`](https://github.com/igrigorik/AgentBoard) | Ilya Grigorik's MV3 side-panel agent that **consumes** WebMCP tools from any tab. Closest existing analogue to stabbur's extension | 128★, last push 2026-08-18, on the Chrome Web Store |
| [`webmcp-types`](https://www.npmjs.com/package/webmcp-types) | Official TS types, blessed by the CG | — |
| Official polyfill | CG **resolved on 2026-08-20** to host one under `webmachinelearning/`; the repo does not exist yet (404 as of 2026-08-26). [#252](https://github.com/webmachinelearning/webmcp/issues/252) | pending |
| Chrome DevTools | Experimental WebMCP panel: list registered tools, invoke manually, validate schemas | ships with the OT |

- `MiguelsPizza/WebMCP` (the original MCP-B, 1,088★) has been dormant since **2025-10-07** — the work
  moved into the standards org and the `@mcp-b` package repo.
- Chrome Web Store has ~9 extensions with "WebMCP" in the title rated 4★+ (cited by Google in its own
  Intent to Experiment as the evidence of "web developer positive signal" — i.e. the strongest
  adoption signal Google could offer was extension-store listings, not sites).

### DHIS2 specifically
Nothing. GitHub code search for `modelContext` across `org:dhis2` returns **0**. There is a small
cluster of *ordinary* DHIS2 MCP servers (`Dradebo/dhis2-mcp` 7★, `brianmituka/dhis2-mcp`,
`EPFLiGHT/talk2yourdata`, and your own `winterop-com/dhis2w-utils`) — i.e. the ecosystem around DHIS2
is going the REST-MCP route stabbur already took. No sign of anyone in the DHIS2 world tracking WebMCP.

---

## 5. Applicability to stabbur

### Could stabbur's extension consume page-exposed WebMCP tools? Yes — with caveats.

The explainer explicitly names extensions as a consumer class: tools "can be invoked by AI agents,
including those built into the browser, hosted in iframes, or **running in extensions**".

But there is **no WebExtensions API for it** ([#74](https://github.com/webmachinelearning/webmcp/issues/74),
open since 2026-02-03, last movement 2026-05-11 — a WECG/WebML CG joint meeting still unscheduled).
Mozilla flagged this as the gating issue for an open agent ecosystem. Today there are exactly two
extension paths:

1. **MAIN-world content script.** Inject at `document_start` with `world: "MAIN"`, call
   `document.modelContext.getTools()` / `executeTool()` in the page's realm, and relay clone-safe
   descriptors out through an ISOLATED-world content script → `chrome.runtime` port → service worker.
   This works because the MAIN-world script *is* same-origin with the page, so it satisfies the
   default `exposedTo` scope without any site cooperation. Both AgentBoard and
   `@mcp-b/webmcp-extension` ship exactly this, and AgentBoard additionally "preserves Chromium's
   native `document.modelContext` when available or installs a standards-shaped polyfill otherwise" —
   so it works on non-OT Chrome and on pages that use the `@mcp-b/global` polyfill.
   Note reillyeon's caveat in #74 (2026-02-10): from the *isolated* world, page-created JS objects
   are not reachable; the MAIN world is required.
2. **`chrome.debugger` + the WebMCP CDP domain** (`WebMCP.enable`), landed in Chromium
   [2026-04-20](https://chromiumdash.appspot.com/commit/71a14cdc4f1378e6d22503b77c577150e0915ef0).
   No content script needed, but it attaches a debugger to the tab — the yellow "extension is
   debugging this browser" infobar, incompatible with a quiet always-on side panel.

### What the plumbing would look like in stabbur

stabbur's constraint: the **backend owns the MCP client and the agent loop**, and today the
extension→backend channel is HTTP + server→client SSE. Page-hosted tools invert that: the tool
*executes in the browser*, so a tool call must travel backend → extension → tab and a result must
come back. That needs a bidirectional channel stabbur does not currently have.

Two shapes:

**(a) The relay shape — no stabbur backend changes at all.**
[`@mcp-b/webmcp-local-relay`](https://github.com/WebMCP-org/npm-packages/tree/main/packages/webmcp-local-relay)
is an **stdio MCP server** that also listens on a localhost WebSocket. Browser side connects over WS;
MCP client side is plain stdio JSON-RPC. It exposes `webmcp_list_sources`, `webmcp_list_tools`, and
the page's tools by name, and tools appear/disappear as tabs open and close.

```
DHIS2 tab (tools on document.modelContext)
   │  MAIN-world script  (either the site's own, or injected by stabbur's extension)
   ▼
localhost WebSocket
   ▼
webmcp-local-relay  ── stdio JSON-RPC ──►  stabbur backend (existing MCP client)
```

stabbur would add it to `.mcp.json` verbatim, like any other server. The agent loop, tool dispatch,
the vision/image handling, the extension — all unchanged. This fits stabbur's "backend is the MCP
client, extension is thin" architecture almost suspiciously well.

Its documented default path asks the *site owner* to add an embed script tag; stabbur's extension would
instead inject the equivalent as a MAIN-world content script, which is what AgentBoard does.

**(b) The native shape — stabbur's own bridge.** stabbur's extension gains MAIN + ISOLATED content scripts,
publishes the tab's tool list to the backend, and stabbur registers those as an in-process MCP-ish tool
namespace whose `execute` round-trips over a WebSocket to the extension. More code, more control,
one fewer moving part at runtime, and it is where you would end up if this ever mattered.

### The hard prerequisite
**stabbur is the consumer, not the site author.** None of this yields a single tool until DHIS2 core
apps call `document.modelContext.registerTool()`. Today: zero DHIS2 code does. The realistic near
paths are (i) a DHIS2 app-platform library so apps opt in — nobody is building one; (ii) stabbur's own
extension injecting a *shim* that registers tools it synthesizes from DHIS2 page state — at which
point WebMCP is just an internal calling convention inside stabbur's own extension and buys you a schema
format, not an ecosystem.

That last observation is the one that should drive the decision: **for stabbur's actual roadmap item
("page actions"), WebMCP contributes essentially nothing that stabbur would not have to build anyway.**
The thing that makes page actions hard is not the tool-call plumbing; it is deciding what an AI
clicking as a logged-in DHIS2 admin is allowed to do. WebMCP has no answer for that — its spec says
so (`readOnlyHint` is advisory, the consent hook is TODO).

---

## 6. Risks and caveats

**Prompt injection is in the threat model and unmitigated.** The spec's own Security and Privacy
Considerations name three vectors: *metadata poisoning* (malicious instructions in tool
descriptions), *output injection* (instructions in tool return values), and *tool implementation as
attack target*. Its concession, quoted approvingly by WebKit: *"there is no guarantee that a WebMCP
tool's declared intent matches its actual behavior."* Mitigations listed are input-length caps,
shared eval datasets, and untrusted-annotation of responses — the first two are not mechanisms and
the third is unspecified. See [#239](https://github.com/webmachinelearning/webmcp/issues/239)
(grammar-level structural mitigation, open, 2026-08-11).

**For stabbur specifically this inverts the current trust posture.** stabbur's DHIS2 tools today come from
an MCP server *you wrote*, with a schema you control. A page-declared tool is a tool description
authored by whatever is running in that tab, fed straight into a model that holds the user's DHIS2
session. On a self-hosted DHIS2 instance the page is nominally trusted — but DHIS2 renders
user-authored content (dashboard item text, interpretations, app names, custom apps installed by
other admins), and a WebMCP tool description is one more field where injected text reaches the model
with tool-calling authority. That is a strictly worse blast radius than the REST-MCP path.

**No consent model.** `readOnlyHint` is a hint the agent may use to *skip* a confirmation.
`toolautosubmit` submits forms without review. Nothing in the spec prevents a page from declaring a
destructive tool as read-only. Consent is delegated entirely to the agent implementer — i.e. to stabbur.

**Same-origin questions are explicitly open.** The security questionnaire's answer to "what should
this questionnaire have asked" is: agents browsing multiple origins may carry state across them; the
spec's *Violation of Same-Origin Boundaries* section is **TODO**. WebKit's sharpest technical point
is that WebMCP is a new cross-origin invocation path whose interaction with COOP/COEP/site isolation
is unexamined.

**Spec churn is high and recent.** `navigator.` → `document.` (May 2026); `getTools()` specced July;
`executeTool()` specced 2026-08-14 and its signature changed 2026-08-17 (JSON string → object) with
another change proposed 2026-08-20 (object → any). Anything stabbur writes against the consumer API this
quarter should be assumed to break.

**Venue and vendor risk.** A CG report with one implementer, one clone of that implementer, one
neutral, and one formal *oppose* from Apple whose objection is not "fix these details" but "the venue
is wrong and the problem is not established." The realistic outcomes range from "ships in Chromium
and stays a Chromium feature" to "gets reshaped into a generic cross-realm call primitive"
([#236](https://github.com/webmachinelearning/webmcp/issues/236)) to "dies with the origin trial at
M156". None of those is remote.

**Betting now is premature.** The one asymmetry worth noting is that the *consumption* pattern is
cheap and already commoditized (AgentBoard, `@mcp-b`), so the cost of waiting is low — you are not
locking yourself out of anything by not moving.

---

## 7. Bottom line

| Question | Answer |
|---|---|
| Real standard? | Incubating CG draft. Not on the Rec track. |
| Shipping? | Origin trial only (Chrome 149–156, Edge 150). Nothing on by default, nowhere. |
| Consumers? | Gemini in Chrome "soon". Field reports show unreliable-to-absent native invocation. |
| Sites? | ~0 measured (111k domains, May 2026). Cloudflare opt-in dev preview (Aug 2026) is the one real lever. |
| DHIS2? | Zero. No signal, no tracking, no library. |
| Can stabbur consume it? | Yes, via a MAIN-world content script or `webmcp-local-relay`. Proven patterns exist. |
| Should stabbur now? | **Watch.** Nothing to consume; consumption is cheap to add later. |

**Trigger conditions to revisit** — any one of these changes the answer:
1. Chrome files an Intent to Ship, or the OT extends past M156 with shipping intent.
2. The WebExtensions API in [#74](https://github.com/webmachinelearning/webmcp/issues/74) lands.
3. DHIS2 (core team or app-platform) opens *any* issue about WebMCP.
4. Cloudflare's preview reaches GA and a measurable fraction of sites turn it on.
5. Mozilla moves from `neutral` to `positive`, or WebKit softens from `oppose`.
