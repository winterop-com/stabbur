# Page actions and the agentic web (WebMCP)

The long-term thread behind one concrete gap: **stabbur's Chrome side panel can act on a DHIS2
instance only through the backend** (`d2w` / the MCP bridge, i.e. REST), while the assistant
sits next to a UI it cannot touch. Extensions like Claude's can drive the page; ours cannot.
This document collects what WebMCP is, whether it answers that, and what we would build
instead. Companion docs: **[`CHROME.md`](CHROME.md)** (extension design), the sourced research
appendix at [`docs/guides/webmcp-assessment.md`](docs/guides/webmcp-assessment.md).

Status: **watch, don't build** (assessed 2026-08-26). The reasoning matters more than the
verdict, because it also decides how we *would* build page actions.

## 1. "WebMCP" names at least three different things

Conflating them makes vendor announcements sound bigger than they are.

| | What it is | Needs browser support? |
|---|---|---|
| **Browser API** | `document.modelContext.registerTool()` — a page declares tools; an agent in the browser discovers and calls them | **Yes** |
| **Site-embedded library** | A `<script>` that exposes the page over an MCP server ([webmcp.dev](https://webmcp.dev/) / `@jason.today/webmcp`, `@mcp-b/webmcp-local-relay`) | **No** — any MCP client connects |
| **Edge injection** | [Cloudflare](https://blog.cloudflare.com/webmcp/) injects the bridge at the CDN, no origin change | No (opt-in per domain) |

**stabbur is already an MCP client.** Flavor 2 is therefore consumable *today with no code
change* — an entry in `.mcp.json`, like any other server. So "vendor X supports WebMCP" is
mostly a non-event for us: we are not missing client support. The binding constraint is on
the other side, and always has been — **someone has to expose tools, and for us that is
DHIS2, which exposes none.**

## 2. Maturity of the browser API (2026-08-26)

- **Chrome 149 origin trial**, or `chrome://flags/#enable-webmcp-testing` locally
  ([Chrome docs](https://developer.chrome.com/docs/ai/webmcp)). No Intent to Ship; nothing on
  by default in any browser. Chrome states the *goal* that "any browser with agentic
  capabilities can implement" it — an aspiration, not a status.
- **No vendor consensus**: [WebKit opposed](https://github.com/WebKit/standards-positions/issues/670)
  (on venue and premise, not details), [Mozilla neutral](https://github.com/mozilla/standards-positions/issues/1412).
  Draft Community Group report, not Rec track.
- **The consumer half is weeks old** — `getTools()` / `executeTool()` specced and re-specced
  through July–August 2026, signature changed mid-August.
- **Adoption ~zero**: a 111k-domain scan found none; Cloudflare's is an opt-in developer
  preview. **DHIS2 has nothing** and is going the REST-MCP route stabbur already took.
- Field reports of agents actually invoking page tools are poor (single-digit successes
  across ~100 trials).

Full evidence with source links: [`docs/guides/webmcp-assessment.md`](docs/guides/webmcp-assessment.md).

## 3. It does not solve "drive the UI" — it inverts it

This is the crux, and it survives any change in maturity.

WebMCP is **not** browser automation. The page declares *semantic functions* precisely so the
agent does **not** click: `registerTool("createDataElement", schema, handler)`. A fully adopted
WebMCP would hand us a list of DHIS2-authored functions that **bypass** the UI — conceptually
much closer to what `d2w` already gives us over REST than to what the Claude extension does.

So: WebMCP is not the missing piece for page actions. If DHIS2 shipped it, we would gain a
second, thinner path to things REST mostly already does.

## 4. We are not blocked on anything external

The extension already runs `chrome.scripting.executeScript` in the target tab — that is how
the "Use my login" PAT mint works. **In-page DOM control needs no new browser API, no spec,
and no DHIS2 cooperation.** Page actions were deferred for a different reason, recorded in
the roadmap from the start: an AI clicking as a logged-in admin is the highest-blast-radius
capability in the design. The blocker is the safety model, not access.

## 5. The design split that makes this tractable

Separate the two things "page actions" bundles together. They have very different value and
risk:

**Navigating and showing** — the genuine gap, and low risk.
"Open Data Entry for this org unit, dataset and period", "take me to that data element's edit
page", "highlight the field you just mentioned". REST cannot do this at all: it is about *the
user's attention*, not data. It is URL construction plus tab navigation plus (at most) scroll
and highlight — no writes, buildable with permissions the extension already holds. This is
what makes the panel feel like it is *in* the app rather than talking about it.

**Acting** — high risk, low marginal value.
REST/`d2w` already does this more reliably than clicking ever will, and it is method-scoped
and auditable. Clicking as an admin buys little and risks much. If we ever want it, it rides
the per-action confirmation gate that already exists across all surfaces.

**Build order:** read-only navigation first; mutating clicks only if a real case appears that
REST cannot serve.

## 6. If we ever did want page-declared tools

Three routes, in decreasing appeal:

1. **DHIS2 adopts it upstream.** The only version that actually pays off, because the value of
   the standard is that a *third party* declares the tools. Nothing we can do but watch.
2. **Per-instance script injection, no fork — but it does not reach the modern UI.**
   DHIS2 exposes [`POST /api/files/script`](https://docs.dhis2.org/en/develop/using-the-api/dhis-core-version-243/settings-and-configuration.html),
   a global custom JS file ([documented since 2.x](https://docs.dhis2.org/archive/en/2.29/developer/html/webapi_ui_customization.html)),
   and the endpoint still exists on 2.42. **Checked 2026-08-26 against
   `play.im.dhis2.org/dev-2-42`: it is a legacy-page mechanism only.** The app-platform SPAs
   (Dashboard, Maintenance, …) serve an `index.html` whose sole script is their own bundled
   `main-*.js` — no custom-script include anywhere. Legacy `dhis-web-commons` assets still
   serve (200), which is what the mechanism was built for. So this cannot inject into the
   apps where the actual UI lives; treat it as closed unless DHIS2 adds a modern hook.
3. **Fork DHIS2 and add `registerTool()` calls.** Bad economics *and* self-defeating:
   - Not one app — Data Entry, Capture, Maintenance, Dashboards, Analytics are separate;
   - instances run 40/41/42/43, so the fork is maintained across releases;
   - we do not control other people's deployments (play42, country prod), so a fork only
     helps where we deploy;
   - **and if we author the handlers, the standard buys us nothing** — we could expose the
     same functions through our own extension bridge with less machinery. Using the WebMCP
     *shape* in a shim is a weak hedge on future native support; given the spec churn above,
     not worth paying for yet.

## 7. Security note (applies to every flavor)

Page-declared tool descriptions are attacker-influenceable text. DHIS2 renders user-authored
dashboard, interpretation and app content, so a page-declared tool surface reaching a model
that holds the user's session is a **worse** trust posture than today's, where every tool
description comes from a server we wrote. The spec itself names metadata poisoning and output
injection as open threats and concedes there is no guarantee a tool's declared intent matches
its behavior. `readOnlyHint` is advisory.

## 8. Revisit triggers

Re-check in ~6 months, or sooner if any of these land:

- Chrome files an Intent to Ship, or extends the origin trial past M156;
- the WebExtensions binding ([webmcp#74](https://github.com/webmachinelearning/webmcp/issues/74)) lands;
- **DHIS2 opens any WebMCP issue** — the one signal that would actually change our calculus;
- Cloudflare reaches GA with real uptake;
- Mozilla turns positive or WebKit softens.
