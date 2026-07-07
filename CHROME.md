# Chrome extension architecture notes

These notes capture the current thinking for adding a Chrome/browser extension that
drives kodo with DHIS2 tools.

Review status: this file has had two design-review passes, the second checked
against the actual kodo code (endpoints, cross-site guard, frontend api client).
The key conclusion did not change: the first extension should be a side-panel
client for local kodo, and kodo should reach DHIS2 through `d2w`/MCP rather than
through browser-cookie relay. The second pass added the "Net-new kodo work"
section, the `/api/chat` request contract + 409 handling, and split the CORS vs
cross-site-guard mechanics. A follow-up added "Driving the active DHIS2 login:
feasibility and verdict" — the honest assessment of using the live browser session
to drive the instance (the SameSite crux, the PAT-minting escape hatch, and the
read-vs-write line). That section was then grounded in a **live measurement**
(play's `JSESSIONID` is `SameSite=lax; HttpOnly; Secure`) and in how deployment /
`dhis.conf` affect it (no SameSite key; set by the reverse proxy) — the practical
conclusion is that the cross-site cookie path is off by default and the cookie only
works from a same-origin DHIS2-tab content script. A "Screenshots, visual context,
and Playwright" section was also added: native Chrome capture + DHIS2 PNG endpoints
for runtime visuals, Playwright reserved for E2E tests and doc screenshots. An
"Extension <-> backend auth" section clarifies that OIDC belongs to remote backends
and the d2w<->DHIS2 hop, never the local extension<->kodo hop. Later notes: tab
grouping for the bound instance (`chrome.tabGroups`), and a "Publishing" section on
shipping a Web Store extension that requires a user-run local service. The
target-metadata endpoint was also de-DHIS2-ified: kept generic (`GET /api/assistant`
from opaque project metadata + an MCP resource) so kodo keeps zero DHIS2 logic,
rather than a DHIS2-named `GET /api/dhis2/target`. A "Cross-browser" section notes
WebExtensions/MV3 as the de facto standard (Chromium family free, Firefox modest via
`sidebar_action` + polyfill, Safari a separate Xcode-wrapped effort). A third pass
(2026-07-04) re-verified every API claim against the code, then fixed the gaps it
surfaced: the model-load step now names `POST /api/load/{name}` (status only
reports, it does not load); the vision/screenshot path is marked schema-accepted
but runtime-unverified; the "credentialed reads in the service worker" instinct was
corrected at its source (Option B) so `SameSite=Lax` reads run in the DHIS2-tab
content script throughout; and Phase 1 gained conversation-history ownership and a
graceful not-connected state.

## Short version

Use the extension as a browser UI/client. Keep kodo as the local backend, model
runtime manager, MCP client, and tool executor.

```text
Chrome side panel
  -> http://127.0.0.1:<pinned-port>/api/chat
      -> kodo agent loop
          -> dhis2w-mcp-bridge/router over stdio
              -> d2w profile
                  -> DHIS2 Web API
```

Do not make the extension itself an MCP host for the first version. Do not pass
browser session cookies down to kodo as the primary DHIS2 credential path. Use
`d2w` profiles and the DHIS2 Web API through the bridge/router.

## What already exists in kodo

kodo already has most of the backend shape needed for an extension:

- `kodo serve --ui --port <port>` gives a stable localhost origin.
- A project with `[project].model` locks the server to that model automatically.
- `kodo serve --model <name>` can also lock the server explicitly.
- `/api/chat` runs the server-side agent loop and MCP tool calls.
- `/api/tools` exposes the attached tool list.
- `/api/status` exposes locked/model/runtime state.
- `/v1/*` is proxied to the loaded runtime, but raw `/v1/chat/completions` does
  not run MCP tools itself. The extension should use `/api/chat` for assistant
  chat.
- `cors_origins` exists for explicit extension/dev origins.
- Mutating `/api`, `/v1`, and `/models` browser requests marked cross-site are
  blocked unless their exact `Origin` is allow-listed.

That means the first extension does not need a new protocol. It can call the same
API the web UI already uses, with a configurable base URL instead of same-origin
`fetch("/api/...")`.

## Net-new kodo work (everything else is reuse)

The chat path reuses existing endpoints, so it is tempting to read this as "no
kodo changes." That is true for chat, but two pieces must actually be *built* in
kodo before the target-instance UX (below) works. They are called out here so they
are not discovered late:

- **A target-metadata endpoint** — does not exist yet. Resolves the active DHIS2
  target (profile, base URL, auth kind, read-only, optional verified block) so the
  side panel does not have to reverse-engineer the MCP command or make the model
  discover it. See "Target instance visibility" for the shape.

  **Keep it generic — do not name it `GET /api/dhis2/target`.** Today kodo has
  *zero* DHIS2 logic (only a suggested `dhis2` MCP preset string in the `cli`
  setup wizard); a DHIS2-named route that imports `dhis2w_core` and parses DHIS2
  profiles would be the first DHIS2 *behavior* in kodo core, breaking kodo's own
  boundary ("generic local-LLM host; DHIS2 knowledge lives in `d2w`") and inviting
  `/api/jira/target`, `/api/github/target`, … next. Prefer a generic
  `GET /api/assistant` (or `/api/context`) that merges two domain-free sources:
  - **opaque project metadata** — a generic `[assistant]`/`[ui]` block in
    `kodo.toml` that kodo echoes verbatim without understanding it (covers
    `base_url`, `auth`, `readonly`, `source`); the DHIS2 project template fills it.
  - **MCP-provided resources** — the DHIS2 bridge publishes target + live
    `verified` (system/info + me) as an **MCP resource**, and kodo exposes a
    generic MCP-resource proxy. DHIS2 logic stays in `d2w`; kodo just forwards.

  This is the best fit with the stated architecture. Only fall back to a
  DHIS2-named endpoint if the generic path proves impractical — a deliberate
  boundary exception, not the default.

- **A generic project metadata block** (not a DHIS2-named `[dhis2]` setting) — the
  static source for the endpoint above, so kodo reads UI metadata from config
  instead of parsing `.mcp.json` server commands. Needs `config.py` / project-loading
  changes, but kept domain-agnostic so kodo never learns the word "dhis2".

Per project rule 4, both the endpoint response and the `[dhis2]` config block must
be **Pydantic models**, not dicts or `@dataclass`.

Everything in Phase 1 (status/tools/chat/speak) is genuinely reuse; these two are
the only real kodo-side additions, and they are Phase 2 (page/target context), not
Phase 1 blockers.

## Localhost serving

Serving kodo on localhost should work well, and is the recommended first target.
Use a pinned port so the extension can remember it:

```bash
kodo serve --ui --port 8000
```

or run inside a DHIS2 kodo project where `[project].model` is set:

```bash
kodo serve --port 8000
```

Once the Chrome extension ID is known, allow-list its origin:

```toml
port = 8000
cors_origins = ["chrome-extension://<extension-id>"]
```

For an unpacked development extension, the extension ID can change if the extension
is reloaded from a different path or generated key. Pin the extension key or be
prepared to update `cors_origins` during development.

Two separate mechanisms are in play here; do not conflate them:

- **Read visibility (CORS).** With `host_permissions` for the localhost origin
  (see the manifest sketch), the extension's `fetch()` bypasses CORS entirely, so
  the extension can already *read* `GET /api/status` etc. without any
  `cors_origins` entry. `cors_origins` is about making responses readable to
  ordinary web pages, which the extension does not need.
- **Mutating cross-site guard.** `POST /api/chat` (and other mutating `/api`,
  `/v1`, `/models` calls) go through kodo's cross-site guard. The guard
  short-circuits and allows the request as soon as the request `Origin` matches an
  allow-listed entry — so allow-listing the exact `chrome-extension://<id>` origin
  is the reliable way to let the extension's POSTs through.

Whether an un-allow-listed extension POST would *otherwise* be blocked depends on
what Chrome sets for `Sec-Fetch-Site` on an extension-page fetch (it may be
`none`, which the guard treats as non-browser and allows). Rather than rely on
that browser-specific behavior, allow-list the origin explicitly — that path is
deterministic regardless of how Chrome tags the request.

Avoid `cors_origins = ["*"]` except for throwaway local read-only testing. A
wildcard makes responses readable cross-origin and weakens the intended localhost
protection, and it is not a substitute for allow-listing the extension origin for
the mutating guard.

## Target instance visibility

Profiles are good for backend auth, but they are not enough for a browser
extension UX. The extension must know, display, and navigate to the DHIS2 instance
that kodo is actually using. Otherwise the side panel is just a prettier terminal.

The product should have an explicit "target instance" concept:

```text
Profile: play42
URL: https://play.im.dhis2.org/dev-2-42
Auth: basic / PAT / OAuth2
Mode: read-only
Status: verified as admin / failed / unknown
```

The side panel should show this target near the composer and provide at least:

- **Open instance**: opens the profile `base_url` in a tab.
- **Open current object**: when the current chat/page context identifies a UID and
  type, deep-link to the relevant DHIS2 app/page where possible.
- **Use this tab**: when the active browser tab is a DHIS2 instance, compare it
  with the backend profile target and warn on mismatch.
- **Verify**: ask kodo to verify the target by calling the DHIS2 `/api/system/info`
  and `/api/me` endpoints through `d2w` (distinct from kodo's own `/api/*` routes
  like `/api/status`; these are DHIS2 Web API paths reached via the bridge).
- **Group the bound tab** (optional polish): put the DHIS2 tab(s) the assistant is
  attached to into a named tab group so it is visible at a glance which tab kodo is
  operating on — the same UX as Claude-in-Chrome's working-tab group. Use
  `chrome.tabs.group({ tabIds })` then `chrome.tabGroups.update(groupId, { title:
  "kodo · play42", color })`; needs the `tabGroups` permission. This reinforces the
  mismatch signal (a tab on a *different* instance is visibly outside the group). It
  is pure presentation — it changes nothing about auth or the tool loop — so keep it
  opt-in and unobtrusive (do not reorganize the user's tabs aggressively), and treat
  it as Phase 2+ alongside the page-context content script.

This implies kodo should expose target metadata to the extension instead of
making the model discover it through a tool call. A small kodo endpoint would be
enough. **Name it generically** (`GET /api/assistant`), not `GET /api/dhis2/target`
— see the boundary discussion under "Net-new kodo work"; the payload below is the
DHIS2 *shape* such a generic endpoint returns for a DHIS2 project, assembled from
opaque project metadata plus an MCP resource, with no DHIS2 code in kodo itself:

```text
GET /api/assistant   (generic route; DHIS2-shaped payload for a DHIS2 project)
  -> {
       "profile": "play42",
       "base_url": "https://play.im.dhis2.org/dev-2-42",
       "auth": "basic",
       "readonly": true,
       "source": "project-toml",
       "verified": {
         "ok": true,
         "version": "2.42.x",
         "username": "admin"
       }
     }
```

Where the fields come from (all keeping DHIS2 logic out of kodo):

- **Static fields** (`base_url`, `auth`, `readonly`, `source`) — from a generic,
  opaque `[assistant]`/`[ui]` block in `kodo.toml` that kodo echoes without
  interpreting. The DHIS2 project template writes it. Cleaner than kodo parsing
  `.mcp.json` server commands or `MCP_ROUTER_CONFIG` for `DHIS2_PROFILE=...`, and it
  keeps the MCP command as pure execution detail.
- **Dynamic `verified` block** (live version + username) — from an **MCP resource**
  the DHIS2 bridge publishes (backed by `system/info` + `me`), which kodo exposes
  through a generic MCP-resource proxy. kodo forwards; `d2w` owns the DHIS2 call.

A DHIS2-named endpoint that imports `dhis2w_core.profile.resolve` directly is the
fallback only if the generic path proves impractical — an explicit boundary
exception, per "Net-new kodo work".

The extension should not silently assume that "current browser tab" and "backend
profile" are the same instance. It should compare origins/base paths:

```text
active tab: https://play.im.dhis2.org/dev-2-42/...
profile:    https://play.im.dhis2.org/dev-2-42
status:     matched
```

If they differ, show a clear mismatch state:

```text
You are viewing play.im.dhis2.org/dev-2-41, but kodo is connected to
play.im.dhis2.org/dev-2-42.
```

That mismatch handling is where a pure profile-backed design otherwise falls
down: the model may query one instance while the user is looking at another.

## Ideal browser-first UX

The ideal user flow is:

```text
1. User logs into a DHIS2 server in Chrome.
2. User clicks the kodo extension.
3. The side panel opens attached to that DHIS2 tab.
4. The panel says "Connected to <this instance>" and shows who the browser user is.
5. kodo either uses an existing matching backend profile or helps create/link one.
```

This is better than starting from a backend profile picker because the user can
see the instance. The visible DHIS2 tab provides orientation, confidence, and a
place to navigate after the assistant answers.

Recommended click behavior:

```text
active tab is DHIS2
  -> extension derives base URL from the tab
  -> a narrow content script in the DHIS2 tab fetches <base>/api/system/info and <base>/api/me (same-origin, cookie rides along)
  -> side panel shows instance name/version/user
  -> side panel asks kodo whether a backend target matches this base URL
```

The credentialed fetch (`fetch(url, { credentials: "include" })`) must run in the
DHIS2-**tab** content script, not the service worker: only the same-site tab
request carries a `SameSite=Lax` session cookie (play is `Lax`; measured). Keep
that content script narrow — fixed endpoints, sanitized results — not an
arbitrary-URL proxy. Full detail in "Driving the active DHIS2 login" below.

Then branch:

### Matching backend profile exists

Use it.

```text
Browser tab: https://play.im.dhis2.org/dev-2-42
kodo target: profile play42 -> https://play.im.dhis2.org/dev-2-42
status: matched
```

The extension can now provide both:

- Visual/browser context from the active tab.
- Real tool use through kodo's `d2w` profile and MCP bridge/router.

This is the best state.

### No matching backend profile

Do not silently fall back to an unrelated profile. Show a setup state:

```text
You are viewing https://dhis2.example.org, but kodo has no matching DHIS2
profile. Choose how to connect tools:

[Use browser context only]
[Link existing profile]
[Create local profile for this instance]
```

`Use browser context only` is useful immediately, but limited: the extension can
fetch a few allowlisted browser-authenticated reads and add them as prompt
context. It should not pretend full MCP tool use is available.

`Link existing profile` lets the user choose one of kodo/d2w's known profiles if
there is a base URL mismatch or naming mismatch.

`Create local profile for this instance` is the smoother long-term path. There
are two possible implementations:

- Ask the user for a PAT/OAuth/basic credential and store it through `d2w profile`
  locally.
- If DHIS2 supports creating a PAT for the current user through the logged-in
  browser session, the extension can request explicit confirmation, create the
  token via a browser-authenticated API call, pass the token once to local kodo,
  and kodo stores it as a `d2w` profile.

The second option gives the desired "login, click extension, connect" flow
without passing raw session cookies to kodo. It is still sensitive because it
mints a durable credential, so it needs an explicit confirmation screen and clear
storage semantics.

### Backend profile points somewhere else

Warn hard, because this is the confusing/dangerous case:

```text
You are viewing https://play.im.dhis2.org/dev-2-42, but kodo tools are connected
to https://staging.example.org.

[Open staging]
[Switch/link profile]
[Use browser context only]
```

Do not let the user casually ask "what am I looking at?" while the tools query a
different server.

## Driving the active DHIS2 login: feasibility and verdict

A natural, ambitious framing of this product is: **the user is already logged into
a DHIS2 server in Chrome; they click the extension; the AI drives *that* instance
as *that* logged-in user, with no separate profile or credential setup.** This
section is the honest feasibility assessment of that specific idea, because it is
the most compelling version of the extension and also the one with the most
subtle failure mode. It sits above Options A-D below: it is the *why* behind
choosing among them.

### The mechanism is real — but only from the DHIS2 tab

The loop "logged into DHIS2 -> click -> AI acts on this instance as this user" is
architecturally real. A Manifest V3 extension reads the active tab's URL, calls the
DHIS2 Web API *as the logged-in browser user* (no separately-entered credential),
and hands the results to an AI (local kodo or a cloud model) that decides the next
call. The AI does not run in the browser; the extension holds the session and the
model drives it.

The catch is **which layer** makes that credentialed call, and it turns on one
cookie attribute: `SameSite`.

### SameSite decides it — and the measured evidence says cross-site fails

The whole "just use my existing login" appeal rests on the DHIS2 session cookie
riding along on the fetch. A fetch from the extension's own origin
(`chrome-extension://…`) to `https://play.dhis2.org` is **cross-site**; a fetch
from a content script *running inside the DHIS2 tab* is **same-site**. A
`SameSite=Lax` cookie rides only the same-site request — so the **service worker /
side-panel page cannot use the login (401/login page), but a tab content script
can**, even though both have host permissions. Host permissions grant cross-origin
*access*, not a cookie on a cross-site request.

**Measured on live play (2026-07-04, `https://play.im.dhis2.org/dev`):**

```text
Set-Cookie: JSESSIONID=...; Path=/dev; Secure; HttpOnly; SameSite=lax
Set-Cookie: SESSION_EXPIRE=...; Path=/; Max-Age=3600; Secure; SameSite=lax
```

Three facts fall out of that one header:

- **`SameSite=lax`** — not carried cross-site; carried from the tab content script.
- **`HttpOnly`** — JS cannot read the cookie value at all (`chrome.cookies` only
  with the `cookies` permission), so "extract and relay the cookie" (Option D) is
  not even a clean read. The only viable path is *issuing the request from a context
  the browser auto-attaches the cookie to*, not copying it.
- **`Path=/dev`** — scoped to the instance sub-path, so any authenticated call must
  preserve that base path.

And `SameSite` is **not** a `dhis.conf` setting — it varies by deployment. DHIS2
core sets no SameSite attribute; `server.https = on` only sets `Secure`. The
`SameSite=lax` on play is added by the reverse proxy / servlet container (NGINX
`proxy_cookie_path`, Tomcat `CookieProcessor sameSiteCookies`). Across deployments
you see **no attribute** (Chrome defaults to Lax -> still not cross-site), **`Lax`**
(play), or **`None; Secure`** (would work cross-site, but non-default and a
deliberate CSRF-weakening opt-in — do not expect it). Net: for essentially every
normal deployment the cross-site fetch will not carry the cookie. Treat "service
worker uses my login" as *off the table by default*.

### Confirm on the target instance (expect it to fail cross-site)

Before building on the cookie path, measure it on the actual deployment at **both**
layers, logged in in the same Chrome profile:

```js
// A) service worker / side panel  (cross-site) — expected: 401 / login on Lax
const a = await fetch("https://<host>/<base>/api/me.json", { credentials: "include" });
console.log("SW", a.status);

// B) content script injected into the DHIS2 tab  (same-site) — expected: 200 on Lax
const b = await fetch("/<base>/api/me.json", { credentials: "include" });
console.log("CS", b.status);
```

- **A = 200** -> this deployment uses `SameSite=None; Secure`; the pure cross-site
  path is unusually available. Rare; do not assume it elsewhere.
- **A = 401, B = 200** (the play case) -> cookie auth only works from the tab
  context; use it for narrow page reads, and use PAT-minting (below) for anything
  durable.
- **B = 401** -> not even same-origin works (Strict, or not logged in); go straight
  to an explicit profile/PAT.

### The robust escape hatch: mint a PAT from the live session

If the cookie will not ride along cross-site (the default case, per the measured
result above), there is a better path that preserves the same "I just logged in,
now it works" UX **without** fragile cookie relay: use the live session *once* to
mint a DHIS2 **Personal Access Token** (DHIS2 has had PATs since ~2.41/2.42), hand
that token to local kodo, and kodo stores it as a `d2w` profile. After that, every
tool call goes through kodo's normal MCP path — reproducible from CLI/TUI/bench,
independent of browser login state, and never touching an ambient cookie.

Note the same layering constraint applies to the mint call: creating the PAT is
itself an authenticated DHIS2 request, so on a `Lax` deployment it must be issued
from the **DHIS2-tab context** (content script / `executeScript`), not the
service worker — the tab request carries the cookie, the cross-site one does not.
The content script makes exactly one narrow call (create token), passes the token
value once to local kodo, and does nothing else.

This is the same flow sketched as "Create local profile for this instance" and is
the section's verdict: it gives the zero-friction feel while dodging both the
SameSite problem and the "an AI loop now holds your ambient browser session"
problem. It is sensitive (it mints a durable credential), so gate it behind an
explicit confirmation screen with clear storage semantics — a one-time gate, not
per-request ambient authority.

### Read vs write: where to draw the line

- **Read-only, on the live session: yes, worth shipping.** Low risk, high value
  ("what am I looking at?", "summarize this program", "is this data element used
  anywhere?"). Even via the cookie path, the blast radius is reads.
- **Writes as the logged-in user: possible, but this is the cautious zone.** It
  hands full account authority — often an admin or superuser on a config/demo
  instance — to an AI tool loop. A prompt injection off a viewed page, or one
  confidently-wrong tool call, then executes *as the user* with no undo. Do it only
  behind explicit per-action confirmation, add it last, and never route it through
  ambient cookie auth (use the PAT/profile path so the tool channel stays the
  auditable one). DHIS2 writes may also require CSRF handling, which the cookie
  path does not get for free.

### Where the AI runs matters too

Driving the live instance says nothing about where the model runs. With **local
kodo** the story is "drive DHIS2 with a *local* model against your live session" —
private, on-device. With a **cloud model**, DHIS2 data leaves the machine to a
third party. For a health-data product that data-residency choice is not a detail;
default to local kodo and treat any cloud model as an explicit, separate decision.

### Bottom line

"Activate on a DHIS2 login and drive it" is the right north star — it is what makes
this more than a generic chat box. The ambition is sound; the realism is that the
cross-site cookie shortcut is off by default, so build it as:

1. read-only first;
2. **PAT-minted-from-the-live-session, from the DHIS2-tab content script**, as the
   credential path — not cookie relay, not a service-worker cross-site fetch. This
   is the robust version of the idea and it works on `Lax`;
3. optionally, narrow same-origin content-script reads for live page context
   (`/api/me`, `/api/system/info`) where a durable token is overkill;
4. writes behind explicit per-action confirmation, added last, always via the
   PAT/profile tool channel (never ambient cookie, and mind CSRF);
5. run the two-layer confirm snippet on each new deployment, expecting A=401/B=200
   — treat A=200 (`SameSite=None`) as the rare exception.

## Browser session vs backend tool auth

The extension should distinguish two channels:

```text
Browser channel:
  active tab + browser credentials
  good for: page context, /api/me, /api/system/info, selected object/page hints

Tool channel:
  kodo + d2w profile + MCP bridge/router
  good for: model-driven DHIS2 queries, multi-step metadata/analytics, repeatable CLI/TUI/tests
```

The browser channel makes the extension feel anchored to what the user sees. The
tool channel makes the assistant actually useful beyond one-off page context. The
best product uses both, with explicit matching between them.

## Local dhis2w-utils wiring

The local `d2w` and MCP bridge/router workspace is:

```text
/Users/morteoh/dev/local/dhis2w-utils
```

For local development, point kodo directly at that workspace rather than relying
on a published `uvx` package:

```json
{
  "mcpServers": {
    "dhis2": {
      "command": "uv",
      "args": ["--directory", "/Users/morteoh/dev/local/dhis2w-utils", "run", "dhis2w-mcp-bridge"],
      "env": { "DHIS2_PROFILE": "play42", "DHIS2_MCP_READONLY": "1" }
    }
  }
}
```

For a mid-sized model, the router may be a better tradeoff than the single CLI
bridge, but it is configured differently. The router fronts an upstream MCP
server through `mcp-router.json` and uses `MCP_ROUTER_READONLY=1` for global
read-only mode. Per-upstream read-only can also be set in the JSON config.

Example `mcp-router.json`:

```json
{
  "servers": [
    {
      "name": "dhis2",
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/Users/morteoh/dev/local/dhis2w-utils",
        "dhis2w-mcp"
      ],
      "env": { "DHIS2_PROFILE": "play42" },
      "readonly": true
    }
  ]
}
```

Then point kodo at the router:

```json
{
  "mcpServers": {
    "dhis2": {
      "command": "uv",
      "args": ["--directory", "/Users/morteoh/dev/local/dhis2w-utils", "run", "dhis2w-mcp-router"],
      "env": { "MCP_ROUTER_CONFIG": "mcp-router.json", "MCP_ROUTER_READONLY": "1" }
    }
  }
}
```

The bridge is still the safest first target for smaller local models because it
exposes one tool, `dhis2_cli`, and keeps the model's tool schema small.

Bridge-specific useful env vars from the actual implementation:

- `DHIS2_PROFILE`: selects the `d2w` profile.
- `DHIS2_MCP_READONLY=1`: fail-closed allowlist of read commands.
- `DHIS2_MCP_PROTECTED_HOSTS`: protected public hosts where writes are refused
  regardless of read-only mode. Defaults include DHIS2 play/debug hosts.
- `DHIS2_MCP_CLI_TIMEOUT`: per-command timeout, default `120` seconds.
- `DHIS2_CLI_BIN`: explicit `d2w` executable override.

## Extending dhis2w-utils if needed

`dhis2w-utils` is a sibling project under the same ownership, so extending it is a
first-class option rather than a fork-and-vendor last resort. The boundary to keep
is: **DHIS2 domain knowledge lives in `d2w`, kodo stays a generic local-LLM host,
and the extension stays a thin client.** When the extension needs a DHIS2-specific
capability that does not exist yet, add it in `dhis2w-utils` and expose it through
the bridge/router (or a small `dhis2w-core` function kodo calls), rather than
reimplementing DHIS2 logic inside kodo.

Relevant packages (workspace at `/Users/morteoh/dev/local/dhis2w-utils`):

- `dhis2w-core` — profile discovery/resolution, auth factory, token store, the
  first-party plugin registry. This is where target-metadata and credential work
  belongs.
- `dhis2w-mcp` / `dhis2w-mcp-bridge` / `dhis2w-mcp-router` — the tool surfaces
  kodo attaches to.
- `dhis2w-client` — the async DHIS2 API client (Basic/PAT/OAuth2).
- `dhis2w-bench` — the model-vs-tools benchmark harness.

Likely additions, in the order the extension is likely to force them:

1. **Target metadata — mostly already covered, confirm before adding.** The
   generic `GET /api/assistant` endpoint (see "Net-new kodo work"; not a
   DHIS2-named route) gets its DHIS2 fields without kodo owning DHIS2 logic: the
   static part from an opaque project `[assistant]` block, and the `verified` block
   (version + username) from a DHIS2 `system/info` + `me` read the bridge already
   exposes via `dhis2_cli` — ideally surfaced as an **MCP resource** kodo proxies
   generically. The cleanest split is for the bridge/`dhis2w-core` to publish that
   target resource, so kodo forwards it rather than importing
   `dhis2w_core.profile.resolve` itself. Add a small `dhis2w-core` helper here only
   if that resource does not exist yet; avoid making kodo parse DHIS2 profile files.

2. **Programmatic profile creation for "create local profile for this instance".**
   The ideal browser-first flow (login, click extension, connect) wants kodo to
   persist a credential without an interactive `d2w profile` prompt. If `d2w` has
   no non-interactive "store this PAT/basic/OAuth credential as profile `<name>`"
   entry point that kodo can call, add one to `dhis2w-core` (writing through the
   existing token store / profiles file), so kodo never re-implements credential
   storage. This is the most probable real d2w change the extension will need.

3. **Read allowlist tuning.** Narrow browser-context reads (Option B) and the
   Verify flow should already fit inside `DHIS2_MCP_READONLY=1`'s allowlist. If a
   needed read command is missing from the read allowlist, widen it in the
   bridge/router config rather than bypassing read-only mode.

4. **Bench coverage.** When the extension exercises new tool-call patterns, add a
   `dhis2w-bench` suite so model-vs-tools behavior stays verified against the same
   bridge/router the extension drives.

Guideline: if a change is "how DHIS2 auth/profiles/API work," it belongs in
`dhis2w-utils`; if it is "how the local model server or extension client behaves,"
it belongs in kodo or the extension. Keep the MCP command as the execution detail
and let `dhis2w-core` own the reusable primitives.

## Recommended extension phases

### Phase 1: side-panel client

Build a Manifest V3 side panel that talks to local kodo.

Responsibilities:

- Store the kodo base URL, for example `http://127.0.0.1:8000`.
- Test connectivity with `GET /api/status`; on failure/refused, show a graceful
  "not connected — start kodo" state rather than a blank panel (this is also the
  #1 Web Store review-pass requirement — see "Publishing").
- Load a model if `/api/status` reports none (`POST /api/load/{name}`), so the
  first chat turn does not 409 (see "Handle 409" below).
- List tools with `GET /api/tools`.
- Own the conversation history: `/api/chat` is stateless (it takes the full
  `messages` array each turn), so the panel accumulates prior turns and resends
  them on every request.
- Send chat turns to `POST /api/chat`.
- Render typed SSE events from `/api/chat`: tokens, reasoning, tool calls, tool
  results, errors, and done.
- Optionally call `/api/speak` for local TTS.

This phase does not need DHIS2 host permissions and does not need cookie access.

`POST /api/chat` request/response contract (from `routers/serving.py`):

```text
POST /api/chat
  body: {
    "messages": [{"role": "user"|"assistant"|"system", "content": "..."}],
    "use_tools": true,                 // false → empty toolset (non-tool models)
    "enabled_tools": ["dhis2_cli"],    // optional allow-list; omit for all tools
    "system_prompt": null              // null → fall back to project prompt; "" → none
  }
  // `messages` is typed list[dict] (serving.py), so `content` may be a plain
  // string OR an OpenAI content-part array (text + {"type":"image_url",...}) for
  // vision models. The schema accepts image parts, but that they actually reach
  // the mlx-vlm runtime is UNVERIFIED — confirm end-to-end before relying on it
  // (see "Screenshots, visual context, and Playwright").
  -> text/event-stream, one JSON object per `data:` frame:
       {"type":"token","text":...}
       {"type":"reasoning","text":...}
       {"type":"tool","kind":"call"|"result","detail":...}
       {"type":"error","detail":...}
       {"type":"done"}
```

**Handle 409 "No model loaded".** `POST /api/chat` returns 409 if no model is
loaded. In a locked project the model loads at server startup; otherwise a model
must be loaded explicitly via **`POST /api/load/{name}`** (`routers/serving.py`;
`GET /api/status` only *reports* state, it does not trigger a load — the web UI
reads status then issues the load itself). The side panel must do the same:

1. `GET /api/status`; if `state` is already loaded, proceed.
2. Otherwise `POST /api/load/{project_model}` — the model name is in the status
   payload as `project_model`.
3. Poll `GET /api/status` until loaded, honoring `runtime_load_timeout` (in the
   status payload, default 600s — a cold runtime start is slow).
4. Only then send the first `POST /api/chat`.

If there is no project model to load, surface a clear "no model" state instead of
sending a chat turn — do not assume the first `POST /api/chat` will succeed.

Scope note: v1 targets a locked-project assistant (`[project].model` set, no
picker). Free-play model switching in the side panel is out of scope for the
first extension.

Streaming caveat: `/api/chat` is a `POST` that returns `text/event-stream`, so
the side panel cannot use `EventSource` (GET-only). Read the stream with
`fetch()` + `response.body.getReader()` and parse `data:` frames manually. The
existing web SPA already does exactly this (the hand-rolled OpenAI SSE fetch
loop); reuse that reader rather than reaching for `EventSource`.

### Phase 2: DHIS2 page context

Add content-script support on DHIS2 pages.

Useful context to send to kodo:

- Current URL.
- Selected text.
- Current app/page route.
- Visible UID or metadata object identifiers when parseable.
- Non-secret page metadata.

The extension should send this as ordinary prompt context. kodo should still use
the configured `d2w` profile and MCP bridge/router for authoritative DHIS2 API
lookups.

### Phase 3: constrained browser-authenticated reads, if needed

If there is a real need to act as the currently logged-in browser user, prefer a
small allowlisted read path over passing raw cookies to kodo.

The read must run in the **DHIS2-tab context** (a narrow content script or
`chrome.scripting.executeScript`), not the service worker — only the tab's
same-origin request carries a `SameSite=Lax` session cookie (see "Driving the
active DHIS2 login"). Safety comes from making that content script narrow and
fixed-endpoint, not from the layer it runs in.

Example shape:

```text
side panel asks a narrow content script in the DHIS2 tab for a specific read
  -> content script fetches "/<base>/api/me.json" same-origin (cookie rides along)
      -> content script returns only the sanitized fields
          -> side panel adds them as prompt context for kodo
```

This keeps browser credentials inside Chrome. It still needs strict operation
allowlists: the content script must expose only fixed, named reads, never an
arbitrary-URL fetch the page (or an injected script) could abuse.

A reverse direction is also possible:

```text
kodo asks extension for a narrow operation
  -> native messaging, local websocket, or polling bridge
      -> extension performs the browser-authenticated read
```

That is a separate security design, not part of the first extension.

## Cookie relay

Passing a DHIS2 browser cookie down to kodo is technically possible in a Chrome
extension with the right permissions, but it is not the recommended primary
design.

Problems:

- A session cookie is ambient browser auth. Sending it to localhost turns kodo
  into a bearer of the user's browser session.
- It couples the assistant to browser login state, SameSite behavior, expiry,
  DHIS2 frontend behavior, and domain rules.
- It does not work cleanly for CLI, TUI, tests, or benchmarks.
- It weakens reproducibility: the same prompt may behave differently depending on
  which browser user is logged in.
- It expands the blast radius of prompt/tool mistakes.

Use `d2w` profiles with PAT/OAuth/basic demo credentials instead. Credentials stay
in the local profile/env/token store, and the model only sees tool results.

Cookie-aware behavior can be considered later for narrow page-context features,
but it should not be the core DHIS2 credential path.

For the fuller feasibility analysis of "drive the live login" — including the
SameSite variable that decides whether a cross-site extension fetch even carries
the cookie, and why minting a PAT from the live session beats relaying the cookie —
see "Driving the active DHIS2 login: feasibility and verdict" above.

## DHIS2 API route choices

There are several meanings of "use the DHIS2 API route". They have different
tradeoffs.

### Option A: kodo -> d2w -> DHIS2 Web API

This is the preferred first route:

```text
kodo -> dhis2w-mcp-bridge/router -> d2w -> DHIS2 Web API
```

This is better than cookie relay for the first product because it is:

- Reproducible from the web UI, extension, TUI, CLI, and benchmark suite.
- Compatible with read-only enforcement via `DHIS2_MCP_READONLY=1`.
- Independent of browser login state.
- Easier to test and debug.
- Aligned with the existing kodo MCP architecture.

This route does not care whether the user has a DHIS2 tab open. The extension is
just a client for local kodo.

### Option B: extension -> DHIS2 Web API -> prompt context

The extension can make cross-origin requests from an extension page or service
worker if the manifest has host permissions for the target DHIS2 origin — but that
grants cross-origin *access*, not the session cookie. The split is:

- **Unauthenticated / token-bearing reads** can run in the extension page or
  service worker.
- **Cookie-authenticated reads (using the live login) must run in a same-origin
  content script in the DHIS2 tab** — a `SameSite=Lax` session cookie rides only a
  same-site request, so the service-worker cross-site fetch will not carry it (the
  measured play case; see "Driving the active DHIS2 login"). Keep that content
  script narrow and fixed-endpoint, never an arbitrary-URL proxy.

Potential use:

```text
side panel/background -> https://play.im.dhis2.org/.../api/me
  -> sanitized JSON added to the user's prompt
      -> kodo answers with that context
```

This helps when the assistant needs quick page/user context from the active
browser session. It does not replace the MCP route for general tool use unless the
extension also becomes a full DHIS2 API proxy, which creates another tool surface
to secure and test.

Pros:

- Can use the current browser login without copying cookies into kodo.
- Useful for "what page/user/object am I looking at?" context.
- Keeps some browser-session concerns inside the extension.

Cons:

- Harder to reuse from CLI/TUI/benchmarks.
- Requires DHIS2 host permissions.
- Needs strict allowlists so a content script or compromised page cannot turn the
  extension into an arbitrary cross-origin fetch proxy.
- Produces prompt context, not true model-driven tools, unless substantial proxy
  machinery is added.

### Option C: DHIS2 `/api/routes` proxy route

`dhis2w-utils` already has a `d2w route` plugin for DHIS2's `/api/routes`
surface. This is a DHIS2-managed reverse proxy: DHIS2 stores a route target,
auth scheme, optional headers, and authority gates; callers invoke
`/api/routes/<uid>/run[/<sub_path>]`, and DHIS2 forwards the request with the
stored upstream auth applied.

That could provide an assistant or tool endpoint inside the DHIS2 auth boundary:

```text
DHIS2 /api/routes/<uid>/run
  -> authenticated as current DHIS2 user
      -> DHIS2 proxies to an external assistant/tool service
          -> route injects upstream auth configured in DHIS2
```

This can help in managed deployments where using DHIS2's own session, RBAC, audit
trail, and deployment controls matters more than local portability.

Pros:

- Clean current-user semantics.
- DHIS2 can enforce its normal access control.
- DHIS2 can broker upstream credentials so frontend code never sees them.
- Easier to fit enterprise/admin expectations.
- Avoids asking users to create local PAT/profile credentials.

Cons:

- Much heavier deployment story.
- Not useful for the local-first CLI/TUI/benchmark workflow.
- Requires DHIS2-side install/configuration.
- If the target is the user's local kodo, DHIS2 would need to reach that local
  machine, which usually fails across NAT/firewalls and is the wrong direction
  for a local-first extension.
- If the target is a shared remote assistant backend, model/runtime isolation,
  tenant boundaries, and data handling become a server product concern.
- Moves the project away from the simple local assistant path.

This is worth keeping as a future managed-deployment path, not the first browser
extension path.

### Option C2: DHIS2 app route returning selected API data

A lighter DHIS2-side app route could return a small curated slice of current-user
data, and the extension could add that to the prompt. This overlaps with Option B
but moves the allowlist into DHIS2 instead of Chrome.

This helps if administrators want to centrally control which DHIS2 data the
assistant may see. It does not replace the local MCP bridge unless it grows into
a full tool API.

### Option D: pass browser cookies to kodo

This is the route to avoid as the default:

```text
extension extracts DHIS2 session cookie
  -> passes cookie to localhost kodo
      -> kodo calls DHIS2 as browser user
```

It can work technically, but it is the weakest design boundary. It transfers
ambient browser authority into a local process and makes behavior depend on a
mutable browser session. If browser-authenticated reads are required, prefer
Option B's constrained extension fetches or a later explicit native-messaging
design. If a durable "use my login" credential is the real goal, prefer minting a
PAT from the live session (see "Driving the active DHIS2 login") over relaying the
cookie — same UX, far stronger boundary.

## Extension API surface

The side panel should primarily use:

```text
GET  /api/status
POST /api/load/{name}        load a model when status shows none (avoids the /api/chat 409)
GET  /api/tools
POST /api/chat
POST /api/speak              optional
GET  /api/voices             optional
POST /v1/audio/transcriptions optional
```

Avoid using raw `/v1/chat/completions` for the main assistant flow because that is
only the runtime proxy. It does not execute kodo's MCP tool loop.

## Manifest permissions sketch

Chrome's extension documentation supports this basic shape: side panels are a
first-class extension UI, extension pages/service workers can perform cross-origin
`fetch()` with host permissions, and cookie access requires the explicit `cookies`
permission plus host permissions. Links:

- <https://developer.chrome.com/docs/extensions/reference/api/sidePanel>
- <https://developer.chrome.com/docs/extensions/develop/concepts/network-requests>
- <https://developer.chrome.com/docs/extensions/reference/api/cookies>
- <https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging>

Phase 1:

```json
{
  "manifest_version": 3,
  "permissions": ["sidePanel", "storage"],
  "host_permissions": ["http://127.0.0.1:8000/*", "http://localhost:8000/*"]
}
```

Phase 2 page context adds DHIS2 host permissions and a content script. The
`https://play.im.dhis2.org/*` entry below is a demo-only pin; for arbitrary DHIS2
hosts, request host access dynamically after a user gesture instead (see the
`activeTab` + `chrome.scripting.executeScript()` note further down):

```json
{
  "permissions": ["sidePanel", "storage", "activeTab", "scripting"],
  "host_permissions": [
    "http://127.0.0.1:8000/*",
    "http://localhost:8000/*",
    "https://play.im.dhis2.org/*"
  ]
}
```

The host permission path component is shown as `/*` for readability, but Chrome
grants host access by scheme/host. Keep endpoint allowlists in extension code and
kodo, not in the manifest pattern.

If the DHIS2 origins are known ahead of time, declare a content script directly:

```json
{
  "content_scripts": [
    {
      "matches": ["https://play.im.dhis2.org/*"],
      "js": ["content-script.js"]
    }
  ]
}
```

If the user can point the extension at arbitrary DHIS2 hosts, keep the content
script out of the static manifest and inject it only after a user gesture with
`chrome.scripting.executeScript()` under `activeTab`. The content script collects
page context (DOM/URL/selection). *Cookie-authenticated* DHIS2 reads run in that
**narrow, fixed-endpoint content script**, not the service worker (only the
same-origin tab request carries a `SameSite=Lax` cookie; see "Driving the active
DHIS2 login"). Either way, never expose an arbitrary-URL fetch the page could
abuse — expose only named operations over the message API.

Only add `"cookies"` if a later design explicitly needs browser-authenticated
reads. Do not include it in the first version.

## Frontend reuse

The existing React UI can likely be reused for the extension side panel if the API
client is made base-URL aware.

Current web UI calls are same-origin, for example:

```ts
fetch("/api/status")
```

The extension build needs:

```ts
fetch(`${baseUrl}/api/status`)
```

The side panel should store `baseUrl` in `chrome.storage` and provide a simple
connection setup/test screen.

## Cross-browser: the WebExtensions standard

"Chrome extension" is really the **WebExtensions API** — a *de facto* standard
(originated by Chrome, adopted by Firefox/Edge/Opera/Brave/Vivaldi/Arc/Safari;
aligned by the W3C WebExtensions Community Group, not a ratified spec). **Manifest
V3** is the common baseline. So targeting more than one browser is realistic, and
kodo's thin-client architecture makes it cheap: the logic is in local kodo, the UI
is the shared SPA, and only a small manifest + panel-mount + a few `chrome.*` calls
are browser-specific.

Reach per target:

- **Chromium family (Chrome, Edge, Brave, Opera, Vivaldi, Arc)** — one MV3 build
  runs on all of them essentially unchanged, `chrome.sidePanel` included. "Chrome
  is fine" already covers ~5 browsers.
- **Firefox** — achievable with modest effort. Same WebExtensions model, but:
  1. `browser.*` (promise-based) vs `chrome.*` — normalize with Mozilla's
     **`webextension-polyfill`**.
  2. Background is an event page, not necessarily a service worker (irrelevant for
     a thin client).
  3. **Side panel is the real catch**: Firefox uses `sidebar_action` (its own
     manifest key + `browser.sidebarAction`), *not* `chrome.sidePanel`. Same UX,
     different mount — so hide it behind a thin "panel mount" adapter.
  4. Adds a `browser_specific_settings` manifest key; separate store (AMO).
- **Safari** — the expensive one, treat as a later standalone effort. It runs
  WebExtensions but must be wrapped in a macOS/iOS app via Xcode
  (`safari-web-extension-converter`) and shipped through the App Store, and it has
  **no built-in persistent side panel**, so the side-panel UX does not map (fall
  back to a popover).

Practical approach: use **`webextension-polyfill`** for the namespace and a
build tool like **WXT** (Vite-based) or **Plasmo** to emit per-browser bundles from
one codebase (they handle the manifest/target differences, including sidePanel vs
sidebar_action). The one early design decision: **abstract the panel mount behind
our own adapter** so the shared SPA does not care which browser hosts it — the SSE
chat loop, target logic, and auth are all already browser-agnostic. Each store
(Chrome Web Store, Firefox AMO, Edge Add-ons, Safari App Store) is a separate
submission and review.

## Extension <-> backend auth (and where OIDC fits)

A common question: extensions that talk to a backend usually log in somehow — is
this OpenID Connect? The answer depends entirely on **local vs remote backend**,
and kodo is local, which changes it.

**Remote / cloud backend (SaaS extension with real user accounts) — yes, OIDC.**
The standard MV3 pattern is OAuth2/OIDC via `chrome.identity.launchWebAuthFlow`:
the extension opens the provider's login page, the provider redirects to
`https://<extension-id>.chromiumapp.org/` with an auth code, the extension
exchanges it using **PKCE** (an extension is a *public client* and cannot ship a
client secret), stores the token in `chrome.storage`, and sends
`Authorization: Bearer <token>` on each call. Needs the `identity` permission.
(`chrome.identity.getAuthToken` is a Google-accounts-only shortcut;
`launchWebAuthFlow` is the generic one.) Cookie-session sharing and pasted
API keys/PATs are the other two common variants.

**Local backend (kodo, `127.0.0.1`) — not OIDC; do not add it.** There is no
identity provider and no multi-user accounts; the trust boundary is "processes on
this machine," and the single local user is already authenticated by being on the
box. So the only mechanisms are:

- localhost binding + the cross-site origin guard (today's design), and
- if writes are ever added, a **shared bearer token** the user configures once in
  both the extension and kodo (the accepted-risk note in "Security defaults").

Bolting an OIDC login onto the extension<->kodo hop would be pure over-engineering.

**Where OIDC actually lives in this stack: the d2w <-> DHIS2 hop, not
extension <-> kodo.** DHIS2 supports OAuth2/OIDC user login and PATs, and
`dhis2w-client` already handles Basic/PAT/**OAuth2**. So identity complexity stays
in DHIS2/d2w, and kodo stays a dumb local host. Two consequences worth noting:

- If a DHIS2 deployment uses **OIDC for user login**, the "mint a PAT from the live
  session" flow (see "Driving the active DHIS2 login") still works unchanged — the
  user is already OIDC-logged-in in the browser, and the same-origin PAT-creation
  call rides that session cookie. OIDC-backed DHIS2 is fully compatible.
- A `d2w` profile can itself hold OAuth2 credentials, so the tool channel works
  against OIDC/OAuth2 DHIS2 instances without the extension ever touching the
  identity flow.

## Screenshots, visual context, and Playwright

Short version: **for the product runtime, Playwright is mostly the wrong tool; for
testing and docs it is the right one.** The reason is the same auth reality as
everything above — the extension operates *inside the user's already-logged-in
Chrome tab*, and Playwright launches a *separate* browser context that does not
share that authenticated session.

Runtime capture, in preference order (all native, no Playwright):

- **What the user is looking at (for a vision model):** `chrome.tabs.captureVisibleTab()`
  returns the visible tab as a PNG in the user's real session. Full-page beyond the
  viewport needs the debugger API (`Page.captureScreenshot`), still native. This is
  the intended path for "look at this dashboard" grounding — kodo already routes
  vision-capable models, so the extension would attach the PNG as an OpenAI
  `image_url` content part on the `/api/chat` message. **Caveat:** `/api/chat`
  accepts content-part arrays structurally (`messages` is `list[dict]`), but that
  image parts actually flow through the agent loop to the `mlx-vlm` runtime is
  **unverified** — verify this end-to-end before building the capture UX on it.
- **Deterministic DHIS2 charts/maps:** fetch DHIS2's server-rendered favorite
  images (`/api/visualizations/{id}/data.png`, `/api/maps/{id}/data.png`) through
  `d2w` as a tool. Reproducible, tab-independent, and reusable from CLI/TUI/bench —
  strictly better than screenshotting a rendered page when a favorite UID is known.
- **DOM/page context:** the content script reads it directly (Phase 2). No browser
  automation needed.
- **Driving the DHIS2 UI in-session** (clicking/navigating as the user): extension
  content scripts / debugger API, because they act within the user's tab and
  session. Playwright cannot cleanly ride the user's real profile here either.

Where Playwright *is* worth adding (dev-only, off the runtime path):

- **E2E tests** of the extension + kodo web UI. Playwright can load an unpacked
  extension into a persistent Chromium context (`--load-extension`) and drive the
  side panel — asserting SSE token/tool rendering, the 409 "no model" flow, target
  mismatch states, etc. Good CI value.
- **Reproducible doc/marketing screenshots** of the UI.

So: `captureVisibleTab` + DHIS2 PNG endpoints for runtime visuals; Playwright in
`devDependencies` for tests and docs, not shipped in the extension.

## Security defaults

- Keep kodo bound to `127.0.0.1` by default.
- Pin a port for extension use.
- Allow-list the exact extension origin in `cors_origins`.
- Keep `DHIS2_MCP_READONLY=1` on by default.
- Use PAT/OAuth/profile credentials for real DHIS2 instances.
- Avoid wildcard CORS.
- Avoid cookie relay.
- Treat page actions and browser-authenticated reads as later, separate, explicit
  features.

Accepted-risk note: the cross-site guard intentionally lets non-browser local
clients through (they send no `Sec-Fetch-Site`), so any process on the machine
can `POST /api/chat` and drive the tool loop. With `DHIS2_MCP_READONLY=1` the
blast radius is reads, which is acceptable for v1. If the tool surface ever gains
writes, add an optional shared bearer token between the extension and kodo rather
than relying on the cross-site guard alone.

## Publishing: a Web Store extension that requires a local service

Requiring a user-run local backend (kodo) is **allowed and common** — password
managers, hardware-wallet bridges, and local-LLM companions all do it. It is not
against Chrome Web Store policy. The things that actually gate approval:

- **`http://127.0.0.1` from the extension is not blocked.** The side panel is a
  `chrome-extension://` secure context, and Chrome treats `127.0.0.1` / `localhost`
  as potentially-trustworthy, so fetching the local `http://` server is fine (a
  non-localhost `http://` target would be blocked as mixed content).
- **Reviewers must be able to test it — the #1 rejection risk.** An extension that
  looks broken without an external service can be rejected. Mitigate with (a)
  reviewer notes in the submission explaining how to run kodo, and (b) a graceful
  "not connected — start kodo" state in the UI so the panel is never blank/broken.
  A read-only or offline demo state helps a reviewer see value without full setup.
- **Justify every permission** in the submission: `host_permissions` for
  `127.0.0.1`/`localhost`, `activeTab`, `scripting`, optional `tabGroups`, and later
  any DHIS2 host. Keep them minimal; broad host access draws scrutiny.
- **MV3 no-remote-code rule.** All logic ships in the package. Talking to a local
  (or remote) LLM server is *data*, not code, so that is fine — but you cannot
  fetch-and-execute remote scripts.
- **Privacy disclosure.** Declare data handling; "talks only to a user-run local
  service, no data leaves the device" is a favorable, simple story — state it
  explicitly.
- **Consider not using the public store.** For a niche DHIS2 tool, **Unlisted**
  visibility (passes review, shareable by link, not searchable) or enterprise /
  self-hosted `.crx` distribution may fit better than a public listing.

Native-messaging alternative: instead of localhost HTTP, an extension can talk to a
locally-installed native host over stdio (how password managers do it). But the
native host installs *outside* the store with a registered manifest — more moving
parts. For kodo, localhost HTTP is simpler and avoids that. Keep native messaging in
reserve only if a future feature needs kodo to call back into the browser (see the
"reverse direction" note in Phase 3).

## Practical first milestone

1. Create a DHIS2 kodo project with a locked model.
2. Configure the local bridge from `/Users/morteoh/dev/local/dhis2w-utils`.
3. Run `kodo serve --port 8000`.
4. Build a minimal MV3 side panel with a base URL setting.
5. Connect to `/api/status`; if it reports no model, `POST /api/load/{name}` and
   poll status until loaded (so the first `/api/chat` does not 409), then
   `/api/tools`, then `/api/chat`.
6. Verify against `play42` with `DHIS2_PROFILE=play42` and
   `DHIS2_MCP_READONLY=1`.

At that point, the extension can already drive DHIS2 through the local model and
MCP bridge without touching browser cookies.
