# The browser extension

stabbur ships a browser extension: an MV3 **side panel** that is a thin client for a locally (or
remotely, over HTTPS) running `stabbur serve`. It lives in `extension/` (WXT + React, reusing the
SPA's components and API client from `frontend/src` through an `@` alias). `extension/README.md`
covers building and loading it; `docs/guides/extension.md` is the user-facing guide. This file is
the design: why the pieces are arranged this way, and the contracts between them.

Companion docs: **[`PAGEACTIONS.md`](PAGEACTIONS.md)** (tools the model runs in the tab),
**[`ROADMAP.md`](ROADMAP.md)** (open work), `docs/guides/browser-session-auth.md` (the measured
evidence behind the auth model below).

## Shape

The extension is a **client**, not a host. The agent loop, the MCP client, tool execution, and the
model runtime all stay in stabbur:

```text
side panel  (chrome-extension://…)
  -> http://127.0.0.1:<port>/api/chat
      -> stabbur agent loop  ->  MCP servers over stdio  ->  the target's API
```

Two consequences worth stating, because both were live options:

- **The extension is not an MCP host.** One agent loop, in stabbur, serving every surface (web,
  panel, CLI, TUI) means tools behave identically everywhere and are testable without a browser.
- **Browser session cookies are not the credential path.** See "Auth" below.

The panel uses `POST /api/chat`, never raw `/v1/chat/completions`: the latter is the runtime proxy
and does not run the MCP tool loop.

### Flavors

One codebase, two builds, selected by `STABBUR_FLAVOR` at build time and baked in as
`__STABBUR_FLAVOR__` (`extension/lib/flavor.ts`): `generic` and `dhis2`. Every feature ships in
both; only naming, branding and copy differ. Output goes to `.output/chrome-mv3` and
`.output/chrome-mv3-dhis2` (plus a test-only `chrome-mv3-e2e` build that adds host permissions for
the live e2e tier and is never shipped).

### The backend it expects

Any `stabbur serve` will do, but the intended one is locked to a single model — either a project
with `[project].model` set, or `stabbur serve --model <name>` — so the panel has no model picker
and a stable `/v1`. Free-play model switching in the panel is out of scope. The panel's default
backend is `http://127.0.0.1:2222`; `validateBaseUrl` (`extension/lib/settings.ts`) requires
`https://` for any non-loopback host, since an extension page blocks mixed content. Settings hold
several backends (id, name, base URL, bearer token) with one active; bindings are keyed per
backend.

## CORS and the cross-site guard are two different mechanisms

Conflating them is the classic way to spend an afternoon on the wrong setting.

**Read visibility (CORS).** With host permissions for the stabbur origin, the extension's `fetch()`
bypasses CORS entirely — it can read `GET /api/status` with no `cors_origins` entry at all.
`cors_origins` (`STABBUR_CORS_ORIGINS`, empty by default, no CLI flag) exists to make responses
readable to *ordinary web pages*, which the extension is not. Avoid `["*"]`.

**Mutating requests (the cross-site guard).** `_cross_site_guard` in `src/stabbur/app.py` blocks
`POST/PUT/PATCH/DELETE` under `/api`, `/v1` and `/models` when the request looks like a browser
request from another site — and, deliberately, one *read* as well: `GET /api/assistant` or
`/api/assistants/*` with a truthy `verify` query, because that spawns MCP subprocesses and calls
the project's verify tool against the live instance. Without that, `<img
src="…/api/assistants/x?verify=1">` on any page would fire a tool run with no preflight in sight.

A request is allowed through when its `Origin` is in `cors_origins` (exact match; a wildcard never
bypasses the guard), when it is same-origin, or when it carries no `Sec-Fetch-Site` and no `Origin`
at all — a non-browser client. So the reliable way to let the panel POST is to allow-list its exact
origin:

```toml
cors_origins = ["chrome-extension://<extension-id>"]
```

An unpacked development extension changes id when loaded from a different path, so pin the
manifest key or expect to update this during development.

**Accepted risk.** Because non-browser local clients are allowed through, any process on the
machine can `POST /api/chat` and drive the tool loop. The mitigation when that matters is
`auth_token` (`STABBUR_AUTH_TOKEN`): set it, and every `/api`, `/v1`, `/models` and docs request
must carry `Authorization: Bearer <token>`. `serve` generates one automatically when binding a
non-loopback address.

## Manifest and permissions

As built (`extension/wxt.config.ts`):

```json
{
  "manifest_version": 3,
  "permissions": ["sidePanel", "storage", "tabs", "activeTab", "scripting"],
  "optional_permissions": ["cookies"],
  "host_permissions": ["http://127.0.0.1/*", "http://localhost/*"],
  "optional_host_permissions": ["http://*/*", "https://*/*"]
}
```

The design rules behind that list:

- **No site is in the static manifest.** Host access for a target site is requested at runtime on
  a user gesture (`chrome.permissions.request`, `extension/lib/hostAccess.ts`), so the extension
  works against any instance without shipping a list of them. `activeTab` grants a transient
  version of the same thing, invisible to `chrome.permissions.contains` and revoked on navigation,
  so every gesture path asks for the durable grant first.
- **`cookies` is optional and requested only for the session fallback.** The PAT path needs no
  cookie access at all, so the default install never holds it.
- **Never expose an arbitrary-URL fetch or an arbitrary injection.** What runs in a tab is a named
  operation implemented in the extension — see the probe reads below and
  [`PAGEACTIONS.md`](PAGEACTIONS.md) rule 1.

## Target visibility and matching

A side panel that cannot tell you *which* instance it is talking to is a prettier terminal. So
stabbur exposes target metadata to the panel rather than making the model discover it:

```text
GET /api/assistants            the sanitized registry
GET /api/assistants?url=<tab>  origin + longest-path-prefix match -> a selected id, or a tie list
GET /api/assistants/{id}       one target  (…?verify=1 runs the live verify probe)
GET /api/assistant             the single-target legacy shape, byte-compatible for older panels
```

**This route is domain-generic on purpose.** stabbur has zero DHIS2 logic; a `GET
/api/dhis2/target` that parsed DHIS2 profiles would be the first, and would invite
`/api/jira/target` next. Instead the static fields come from an opaque `[assistant]` /
`[[assistants]]` block in `stabbur.toml` that stabbur echoes without interpreting, and the live
`verified` block comes from a **project-declared MCP tool call** (`[assistant.verify]`) whose
result stabbur forwards. A `dhis2://target` MCP *resource* plus a generic resource proxy is the
intended long-term replacement for that tool call; it can land without changing this contract
(tracked in `ROADMAP.md`).

The panel compares the active tab against the declared targets itself
(`extension/lib/tabTarget.ts`): `match()` requires exact origin equality plus path containment,
and `selectTarget()` ranks targets by longest matching base path, auto-selecting only a strictly
unique top rank — a tie leaves the choice to the user. That comparison exists on both sides
(`GET /api/assistants?url=` is its Python twin) and is pinned to parity by mirrored fixtures, so
the panel and the server can never disagree about which instance a tab belongs to. Mismatch is a
visible banner state, never a silent fallback: letting the user ask "what am I looking at?" while
the tools query a different server is the failure this whole section exists to prevent.

## Auth: act as whoever is logged into the tab

**The verdict, decided and shipped: the cross-site cookie path is off by default, so mint a token
from the live session instead of relaying a cookie.** A `SameSite=Lax` session cookie rides only a
same-site request, so the service worker and side panel cannot use the login while an in-tab
injection can — host permissions grant cross-origin *access*, not a cookie on a cross-site
request. The measurement behind that, the deployment variables, and the probe to re-run on a new
instance are in `docs/guides/browser-session-auth.md`.

What that means in the shipped flow ("Use my login", `extension/components/BindFlow.tsx`,
`lib/bindRecipe.ts`, `lib/binding.ts`, `lib/bindApi.ts`):

1. When the active tab matches a target, the panel offers **Use my login** behind a consent card
   that states the scope in plain words — read-only or read-write, its expiry, and where it is
   stored.
2. On confirm, the credential is minted **entirely in the target tab's own security context**
   (`chrome.scripting.executeScript`, MAIN world, a fetch with `credentials: "include"` to the
   path the project's bind recipe declares). The raw token never touches the service worker;
   only the extracted status, token and credential id come back.
3. The panel installs it through stabbur: `POST /api/assistants/{id}/bind` (compat:
   `/api/assistant/bind`), which runs the named mode's argv with the secret passed in
   `secret_env` — never in argv — redacted from captured output, serialized on a lock, with the
   verify cache invalidated after. **stabbur still learns no DHIS2**: the mint paths, payload and
   extraction fields are an opaque, sanitized recipe echoed from project config, and a mode's argv
   and `secret_env` stay server-side.
4. The banner then shows who the tools are acting as, with **Unbind** (revoke in the tab, remove
   the profile) and **Rebind**.

**PAT-first, session fallback.** A mint that comes back 404/405/501 (no PAT support) offers a
session-cookie fallback: it requests the optional `cookies` permission on a user gesture, reads the
session cookie, and installs it as a `session`-auth profile; a background listener refreshes that
binding when the cookie changes and marks it stale when it disappears. A 401 means "sign in first".
The fallback is second-class on purpose — a session credential cannot be method-scoped, so the
confirmation gate is its only guardrail — and PAT scope is fixed at mint, so a read-only token
cannot escalate: a write-enabled assistant triggers an explicit re-mint behind the allow-writes
consent.

**Two channels, kept distinct.** The *browser channel* (the tab, the live session) is good for page
context and identity: who is viewing this tab. The *tool channel* (stabbur + profile + MCP) is what
the model actually drives. The panel labels both, because after a bind they converge and before one
they do not — "who am I" is answered from the browser user, while the tool account is reported only
when asked.

**Where OIDC fits: not here.** The extension↔stabbur hop is local, single-user, with no identity
provider — bolting OIDC onto it would be over-engineering; localhost binding, the cross-site guard,
and the optional bearer token are the whole mechanism. Identity complexity lives one hop further
out, between `d2w` and the instance, where OAuth2/OIDC and PATs already exist. An OIDC-logged-in
user mints a PAT exactly the same way, because the in-tab call rides whatever session the browser
already has.

**Writes** ride the per-action confirmation gate below, never ambient authority. An optional
`X-XSRF-TOKEN` double-submit is captured at a session-mode write bind and passed into the stored
profile; it is inert where the instance issues no such cookie, so it future-proofs a hardened
deployment rather than being required today.

## The `/api/chat` contract

`POST /api/chat` (`src/stabbur/routers/serving/chat.py`) is stateless — it takes the full
`messages` array every turn — so the panel owns conversation history and resends it.

Request body (all optional except `messages`):

```text
messages          list of {role, content}; content may be a string or an OpenAI content-part array
max_tokens        int; omitted -> the server's default cap (<=0 disables)
temperature, top_p, top_k, min_p, repeat_penalty   omitted -> the model's recommended sampling
response_format   OpenAI structured output; cannot be combined with use_tools (400)
use_tools         bool, default true; false -> no toolset (non-tool models)
enabled_tools     allow-list of namespaced tool names; omitted -> all
target            registry target id this turn routes to; narrows tools, drives the confirm default
system_prompt     string ("" for none) overrides the project prompt; omitted -> project default
confirm_tools     "all" | "writes" | "none"; omitted -> the target's default
reasoning         thinking budget for reasoning models
page_actions      action names this client can execute in the tab (see PAGEACTIONS.md)
```

The response is `text/event-stream`, one JSON object per `data:` frame. This is the full frame
list — `chat.py` points here for it:

```text
{"type":"token","text":...}
{"type":"reasoning","text":...}
{"type":"tool","kind":"call"|"result","detail":...}
{"type":"usage","usage":{...}}                     per round; OpenAI usage + llama.cpp timings
{"type":"confirm","id":...,"tool":...,"args":{...}}          gated call; awaits /api/chat/confirm
{"type":"confirm_resolved","id":...,"approved":...,"reason":"user"|"timeout"}
{"type":"page_action","id":...,"action":...,"args":{...}}    runs in the TAB; see PAGEACTIONS.md
{"type":"error","detail":...}
{"type":"done","finish_reason":"stop"|"length"|"tool_calls"|...}
```

`done` is always last. Its `finish_reason` appears only when the runtime reported one, so a parser
that ignores unknown keys reads it as the bare `{"type":"done"}` it read before. **`"length"` means
the reply was cut off at `max_tokens`** — indistinguishable from a complete answer from the frames
alone, and (when the whole budget went to reasoning) from an empty one: zero token frames, then a
clean `done`. The server also emits a plain `error` frame saying so just before `done`, so a client
that renders errors already tells the user what happened.

**Streaming caveat.** `/api/chat` is a `POST` returning SSE, so `EventSource` (GET-only) cannot
read it. Use `fetch()` + `response.body.getReader()` and parse `data:` frames — which is what the
shared SPA client already does.

### The confirmation gate

A gated tool call pauses on a `confirm` frame until the panel POSTs the user's answer:

```text
POST /api/chat/confirm   {"id":"<call-id>","approve":true|false}
```

Note the asymmetry, easy to get wrong from either end: the **request body** field is `approve`,
while the `confirm_resolved` **frame** reports `approved` (plus `reason`, saying whether a human
answered or the gate timed out). The `confirm` frame carries the call's `args` — not a `detail`,
which is the tool-activity frame's field.

The backend holds a per-generation future per gated call; nothing resolving within
`STABBUR_CONFIRM_TIMEOUT` (300s) **auto-denies**, fail-safe. A declined call returns `error: user
declined this action` and the model continues rather than crashing. Policy is generic and
fail-safe: stabbur reads each MCP tool's `readOnlyHint` and confirms anything not marked read-only,
an *unannotated* tool included. The non-interactive `stabbur chat -p` has nobody to ask, so it
denies gated writes unless `--allow-writes` is passed.

### Page actions

`page_action` frames are the model calling a tool that runs in the tab, resolved by `POST
/api/chat/page-action`. The contract, the safety model, and what is built are in
[`PAGEACTIONS.md`](PAGEACTIONS.md).

### Model loading, and the 409

`POST /api/chat` returns **409** when no model is loaded. In a locked project the model loads at
server startup; otherwise the panel must load one itself, because `GET /api/status` only *reports*
state:

1. `GET /api/status`; if loaded, proceed.
2. Otherwise `POST /api/load/{name}` — the name is in the status payload as `project_model`.
3. Poll status until loaded, honoring `runtime_load_timeout` (in the payload, default 600s; a cold
   runtime start is slow).
4. Only then send the first chat turn.

With no project model to load, show a "no model" state rather than sending a turn that will 409.

## Runtime visuals: native capture, not Playwright

The extension operates inside the user's already-logged-in tab; Playwright launches a separate
browser context that does not share that session. So for the product runtime the capture paths are
all native — `chrome.tabs.captureVisibleTab()` for what the user is looking at (attached to the
chat message as an OpenAI `image_url` content part, for a vision model), server-rendered image
endpoints fetched through a tool where the site has them (reproducible, tab-independent, reusable
from the CLI and benchmarks), and the page read for structure. Playwright's place is dev-only: the
e2e tiers below, and reproducible doc screenshots.

## Tests

`extension/e2e/` has three Playwright projects, all driving the real unpacked extension:

- **`mock`** (`make extension-e2e`) — hermetic, against an in-process fake stabbur API
  (`e2e/mockServer.ts`). Covers connection and backend switching, the chat stream, the confirm
  gate, bind (read and write), tab matching and the registry, page text, page actions, the
  cross-site guard, and appearance. `tabtarget-parity.spec.ts` pins the client's tab matching to
  the server's.
- **`live`** (`make extension-e2e-live`) — the full loop against a real model and a real instance,
  read-only against the public demo; writes need a non-protected instance.
- **`prompts`** — the prompt catalog harness behind `docs/guides/extension-prompts.md`.

## Publishing a Web Store extension that needs a local service

Requiring a user-run local backend is allowed and common (password managers, hardware-wallet
bridges, local-LLM companions). What actually gates approval:

- **`http://127.0.0.1` is fine.** The panel is a `chrome-extension://` secure context and Chrome
  treats loopback as potentially-trustworthy. A non-loopback `http://` target would be blocked as
  mixed content — which is why the panel enforces `https://` for remote backends.
- **Reviewers must be able to test it — the top rejection risk.** Hence reviewer notes explaining
  how to run stabbur, and a graceful "not connected — start stabbur" state so the panel is never
  blank or broken.
- **Justify every permission** in the submission, and keep them minimal; broad host access draws
  scrutiny. The runtime-requested host access above is the shape that survives this.
- **MV3 forbids remote code.** All logic ships in the package; talking to a local LLM server is
  data, not code.
- **Privacy disclosure**: "talks only to a user-run local service, no data leaves the device" is a
  simple, favorable story — state it explicitly.
- **Consider Unlisted** visibility (passes review, shareable by link, not searchable) over a public
  listing for a niche tool.

Native messaging (stdio to a locally installed host) is the alternative to localhost HTTP. It
installs *outside* the store with a registered manifest — more moving parts — so keep it in reserve
for a future feature that needs stabbur to call back into the browser.

## Cross-browser

"Chrome extension" is really the **WebExtensions** API, a de facto standard, with **MV3** the
common baseline. stabbur's thin-client shape makes porting cheap: the logic is in stabbur, the UI is
the shared SPA, and only the manifest, the panel mount, and a few `chrome.*` calls are
browser-specific.

- **Chromium family** (Chrome, Edge, Brave, Opera, Vivaldi, Arc) — one MV3 build, `chrome.sidePanel`
  included.
- **Firefox** — modest effort: `webextension-polyfill` for the `browser.*` namespace, a
  `browser_specific_settings` key, a separate store, and the real catch — Firefox uses
  `sidebar_action`, not `chrome.sidePanel`, so keep the panel mount behind an adapter.
- **Safari** — a standalone effort: WebExtensions, but wrapped in an app via Xcode and shipped
  through the App Store, with no built-in persistent side panel, so the UX does not map.

WXT already emits per-browser bundles from this one codebase.

## Considered and rejected

The DHIS2-specific route question had four candidates. **Chosen: stabbur → MCP bridge/router → `d2w`
→ the Web API** — reproducible from every surface, enforceable read-only, independent of browser
login state, and aligned with the MCP architecture stabbur already has. Rejected: *extension →
Web API → prompt context* as the primary path (produces prompt context, not model-driven tools, and
is not reusable from the CLI or benchmarks — it survives only as the narrow, project-declared probe
reads); *DHIS2's `/api/routes` proxy* (clean current-user semantics and server-side RBAC, but a
heavy deployment story, and it points the wrong way for a local-first tool — the instance would
have to reach the user's machine); and *relaying browser cookies to stabbur* (technically possible,
the weakest boundary of the four, and superseded entirely by minting a token from the live
session).

## Open work

Tracked in `ROADMAP.md` under "Browser extension follow-ups" — the sign-in-first bind state,
act-as-you by default, write-scope re-mint, multi-target panel wiring, the `dhis2://target` MCP
resource, and packaging. Also open there: the extension still verifies a target with `GET
…?verify=1`, which the cross-site guard treats as mutating; POST routes exist server-side and the
panel should migrate to them.
