# WebMCP — decision record

**Status: watch, don't build** (assessed 2026-08-26). Evidence with source links:
[`docs/guides/webmcp-assessment.md`](docs/guides/webmcp-assessment.md). What we built instead:
[`PAGEACTIONS.md`](PAGEACTIONS.md).

## What "WebMCP" names

Three different things, which is why vendor announcements sound bigger than they are.

| | What it is | Needs browser support? |
|---|---|---|
| **Browser API** | `document.modelContext.registerTool()` — a page declares tools; an agent in the browser discovers and calls them | **Yes** |
| **Site-embedded library** | A `<script>` that exposes the page over an MCP server ([webmcp.dev](https://webmcp.dev/), `@mcp-b/webmcp-local-relay`) | **No** — any MCP client connects |
| **Edge injection** | [Cloudflare](https://blog.cloudflare.com/webmcp/) injects the bridge at the CDN, no origin change | No (opt-in per domain) |

## Why stabbur does not build on it

**1. We are not the side that is missing anything.** stabbur is already an MCP client, so flavor 2
is consumable today with **no code change** — an entry in `.mcp.json`, like any other server. "Vendor
X supports WebMCP" is therefore mostly a non-event for us. The binding constraint is on the other
side: someone has to expose tools, and for us that is DHIS2, which exposes none.

**2. It inverts UI control rather than providing it.** This is the crux, and it survives any change
in maturity. WebMCP is not browser automation: a page declares *semantic functions* precisely so
the agent does **not** click. A fully adopted WebMCP would hand us site-authored functions that
**bypass** the UI — conceptually much closer to what `d2w` already gives us over REST than to
driving the page. So it is not the missing piece for page actions.

**3. Maturity does not support building on it yet.** Chrome origin trial only, no Intent to Ship,
nothing on by default anywhere; [WebKit opposed](https://github.com/WebKit/standards-positions/issues/670),
[Mozilla neutral](https://github.com/mozilla/standards-positions/issues/1412); a Community Group
draft, not Rec track; the consumer half re-specced through mid-2026. Adoption is near zero (a
111k-domain scan found none), DHIS2 has nothing and is going the REST-MCP route we already took,
and field reports of agents successfully invoking page tools are poor.

**4. Authoring the tools ourselves buys nothing.** The value of the standard is that a *third
party* declares the tools. Forking DHIS2 to add `registerTool()` calls means maintaining a fork of
several separate apps across four release lines, for deployments we do not control — and if we
author the handlers anyway, our own extension bridge does the same job with less machinery. The
one no-fork injection point, DHIS2's `POST /api/files/script`, was checked on 2026-08-26 and is
**legacy-page only**: the app-platform SPAs load nothing but their own bundle, so it cannot reach
the apps that matter.

**5. A page-declared tool surface is a worse trust posture than today's.** Tool descriptions would
be attacker-influenceable text reaching a model that holds the user's session, where today every
description comes from a server we wrote. The spec itself names metadata poisoning and output
injection as open threats and concedes no guarantee that a tool's declared intent matches its
behavior; `readOnlyHint` is advisory.

Note that page actions themselves were never blocked on any of this: the extension already runs
`chrome.scripting.executeScript` in the tab. The blocker was always the safety model, which is
where [`PAGEACTIONS.md`](PAGEACTIONS.md) starts.

## Revisit triggers

Re-check in ~6 months, or sooner if any of these land:

- Chrome files an Intent to Ship, or extends the origin trial past M156;
- the WebExtensions binding ([webmcp#74](https://github.com/webmachinelearning/webmcp/issues/74)) lands;
- **DHIS2 opens any WebMCP issue** — the one signal that would actually change our calculus;
- Cloudflare reaches GA with real uptake;
- Mozilla turns positive or WebKit softens.
