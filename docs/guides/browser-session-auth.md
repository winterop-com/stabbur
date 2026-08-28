# Driving the live browser login — measurement and verdict

Research appendix for `CHROME.md` (in the repository root), which states the conclusion. This file
holds the evidence behind it, and the probe to re-run on a new deployment.

**The idea being tested.** The user is already logged into an instance in Chrome; they click the
extension; the assistant drives *that* instance as *that* logged-in user, with no separate
credential setup. It is the most compelling version of the extension and also the one with the
subtlest failure mode.

**Verdict: the cross-site cookie path is off by default. Mint a token from the live session
instead.** That is what shipped.

## The mechanism is real — but only from the target tab

An MV3 extension can read the active tab's URL and call the site's API *as the logged-in browser
user*. The catch is **which layer** issues that call, and it turns on one cookie attribute:
`SameSite`.

A fetch from the extension's own origin (`chrome-extension://…`) to `https://<host>` is
**cross-site**; a fetch from a script *running inside the site's tab* is **same-site**. A
`SameSite=Lax` cookie rides only the same-site request — so the service worker and side-panel page
cannot use the login (401, or a login page), while an in-tab injection can, even though both hold
host permissions. **Host permissions grant cross-origin access, not a cookie on a cross-site
request.**

## Measured on the public DHIS2 demo (2026-07-04)

```text
Set-Cookie: JSESSIONID=...; Path=/dev; Secure; HttpOnly; SameSite=lax
Set-Cookie: SESSION_EXPIRE=...; Path=/; Max-Age=3600; Secure; SameSite=lax
```

Three facts fall out of that one header:

- **`SameSite=lax`** — not carried cross-site; carried from an in-tab request.
- **`HttpOnly`** — JS cannot read the value at all (`chrome.cookies` only, with the `cookies`
  permission), so "extract and relay the cookie" is not even a clean read. The only viable path is
  *issuing the request from a context the browser attaches the cookie to*, never copying it.
- **`Path=/dev`** — scoped to the instance sub-path, so any authenticated call must preserve that
  base path.

And `SameSite` is **not** a DHIS2 setting: DHIS2 core sets no SameSite attribute (`server.https =
on` only sets `Secure`), so the attribute above comes from the reverse proxy or servlet container
(NGINX `proxy_cookie_path`, Tomcat `CookieProcessor sameSiteCookies`). Across deployments you see
**no attribute** (Chrome defaults to Lax, so still not cross-site), **`Lax`**, or **`None;
Secure`** — the last would work cross-site but is non-default and a deliberate CSRF-weakening
opt-in. Net: for essentially every normal deployment the cross-site fetch will not carry the
cookie.

## Re-running the probe on a new deployment

Logged in in the same Chrome profile, at both layers:

```js
// A) service worker / side panel  (cross-site) — expected: 401 / login on Lax
const a = await fetch("https://<host>/<base>/api/me.json", { credentials: "include" });
console.log("SW", a.status);

// B) injected into the site's own tab  (same-site) — expected: 200 on Lax
const b = await fetch("/<base>/api/me.json", { credentials: "include" });
console.log("CS", b.status);
```

- **A = 200** — this deployment uses `SameSite=None; Secure`; the cross-site path is unusually
  available. Rare; do not assume it elsewhere.
- **A = 401, B = 200** — the measured case. Cookie auth works only from the tab context.
- **B = 401** — not even same-origin works (Strict, or not logged in); go straight to an explicit
  profile credential.

## Why minting beats relaying

Using the live session *once* to mint a Personal Access Token, handing that to local stabbur, and
storing it as a `d2w` profile preserves the "I just logged in, now it works" feel while dodging two
problems at once: the SameSite one above, and the "an AI loop now holds your ambient browser
session" one. After the mint, every tool call goes through the normal MCP path — reproducible from
the CLI, TUI and benchmarks, independent of browser login state, and never touching an ambient
cookie.

The same layering constraint applies to the mint call itself: creating the token is an
authenticated request, so on a `Lax` deployment it must be issued from the tab, not the service
worker. That is what `extension/lib/bindRecipe.ts` does — one narrow call in the tab's own
security context, with only the extracted token crossing back.

Relaying the cookie to stabbur instead was rejected for reasons independent of SameSite: it turns a
local process into a bearer of the user's browser session, couples the assistant to login state and
expiry, does not work from the CLI/TUI/benchmarks at all, and makes the same prompt behave
differently depending on who is logged in.
