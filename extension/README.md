# stabbur extension

A Chrome MV3 side-panel extension that is a thin client for a locally running
`stabbur serve`. Built with WXT + React 19 + Tailwind v4.

It reuses the shared frontend source directly (`frontend/src/{api,lib,components}`)
via the `@` alias, so the chat renderer (Markdown, code highlighting, mermaid
diagrams, tool-call chips) and the API/HTTP client are the same code the web UI
ships. The panel is one workspace in the repo-root Bun monorepo, so its React and
other shared deps are hoisted and deduped against the frontend.

## Develop

From the repo root (installs the whole Bun workspace, including this package):

```
bun install
```

Then, from `extension/`:

```
bun run dev     # WXT dev server with HMR (loads .output/chrome-mv3-dev)
bun run build   # production build -> .output/chrome-mv3
```

The panel bundles the shared Markdown stack, so this package depends on
`react-markdown`, `remark-gfm`, `rehype-highlight`, `rehype-raw`,
`rehype-sanitize`, `highlight.js`, and `mermaid` (mermaid is lazily code-split and
only loaded when a diagram is actually rendered), plus `tw-animate-css` for the
shared theme. These mirror the versions in `frontend/package.json`; keep them in
sync when the frontend bumps them.

## Flavors (generic vs stabbur for DHIS2)

One codebase ships two builds. Only branding and a few words of in-app copy differ —
**every feature is in both**:

| | Generic | DHIS2 |
| --- | --- | --- |
| Build | `bun run build` | `bun run build:dhis2` |
| Env | `STABBUR_FLAVOR` unset | `STABBUR_FLAVOR=dhis2` |
| Output dir | `.output/chrome-mv3` | `.output/chrome-mv3-dhis2` |
| Manifest name | `stabbur` | `stabbur for DHIS2` |
| Description | generic side-panel copy | DHIS2 assistant copy |
| Icon | dark `k` mark | same mark + blue corner accent |
| Panel header | `stabbur` | `stabbur for DHIS2` |
| Disconnected hint | generic `stabbur serve` | mentions a DHIS2 project + docs link |

The flavor is selected by the `STABBUR_FLAVOR` env var, read in `wxt.config.ts` (which
switches the manifest name/description/icon and the output dir) and baked into the
bundle via a Vite `define` (`__STABBUR_FLAVOR__`). App code branches copy through
`lib/flavor.ts` (`flavor()`, `isDhis2Flavor()`, `flavorTitle()`).

Icons live in `public/icon/{generic,dhis2}-{16,32,48,128}.png`, generated
deterministically by `bun run scripts/gen-icons.ts` (re-run only when the mark
changes; the PNGs are committed). The E2E tests and the mock screenshots default to the
generic build path (`.output/chrome-mv3`); the docs-screenshots harness uses the DHIS2
build so the panel is branded in the images.

## Load into Chrome

1. Run a stabbur server on a pinned port, e.g. `stabbur serve --port 8000`.
2. `bun run build` (generic) or `bun run build:dhis2` (DHIS2) in `extension/`.
3. Open `chrome://extensions`, enable Developer mode.
4. "Load unpacked" -> select `extension/.output/chrome-mv3` (or
   `extension/.output/chrome-mv3-dhis2`).
5. Click the stabbur toolbar icon to open the side panel.

## Connect it to stabbur

Open the panel's Settings (gear icon) and set the base URL (default
`http://127.0.0.1:2222`) and, if the server requires one, an access token.

Read-only requests (status, tools, chat streaming) work immediately. Mutating
requests (loading a model) are blocked by stabbur's cross-site guard until you allow
this extension's origin. Add the following line to your stabbur config and restart
`stabbur serve` (the panel shows the exact line with a copy button):

```
cors_origins = ["chrome-extension://<extension-id>"]
```

Note: an unpacked extension's id is derived per machine, so it differs on every
checkout unless you pin a `key` in the manifest. Copy the id from the panel's
Settings view (or `chrome://extensions`) on the machine you are using.

## Remote / cloud stabbur

The panel can point at a stabbur that is not on this machine, but an extension page
cannot make plain-`http` requests to a remote host (mixed-content block). So a
non-loopback base URL **must** use `https`. The Settings view enforces this: a
loopback host (`127.0.0.1` / `localhost` / `[::1]`) may use `http`, any other host
requires `https://`.

To connect to a remote stabbur:

1. **Serve stabbur behind TLS.** Terminate HTTPS in front of `stabbur serve` (a reverse
   proxy such as Caddy/nginx, or a tunnel that provides an `https://` URL). The
   panel talks to that `https://` origin.
2. **Set an auth token.** When stabbur binds a non-loopback interface it refuses to
   run without auth and auto-generates an `auth_token`; otherwise set one
   explicitly. Paste it into the panel's Settings (Access token) — it is sent as a
   `Bearer` header on guarded requests, and the panel prompts for one when the
   server answers `401`.
3. **Allow the extension origin.** Add this extension's origin to stabbur's
   `cors_origins` (the panel shows the exact line with a copy button), then restart
   `stabbur serve`:

   ```
   cors_origins = ["chrome-extension://<extension-id>"]
   auth_token = "<your-token>"
   ```

If you would rather not expose stabbur publicly, forward it to loopback over SSH
(`ssh -L 2222:127.0.0.1:2222 user@host`) and point the panel at
`http://127.0.0.1:2222` as usual — loopback is allowed over `http`.

## End-to-end tests

Playwright drives the built extension in a real Chromium side panel. Two tiers,
selected by `--project`:

- **mock** (`bun run e2e`, or `make extension-e2e`) — fast and hermetic. Each spec
  starts a tiny in-process Node server that speaks the stabbur API contract
  (`/api/status`, `/api/load`, `/api/chat` SSE, `/api/assistant`) with scriptable
  scenarios, so it needs no stabbur, no model, and no network. Covers the connection
  lifecycle (disconnected auto-retry, needs-token, needs-model -> Load -> loading
  -> ready), chat streaming (tokens, reasoning, tool call/result chips, markdown),
  stream abort, the cross-site (403) `cors_origins` hint, the `/api/assistant`
  banner + verify (ok / error / 404), and the tab-match banner.
- **live** (`bun run e2e:live`, or `make extension-e2e-live`) — the real thing:
  it writes a throwaway stabbur project bound to a local GGUF model plus the DHIS2
  CLI bridge (pointed at the public **play** demo), runs `stabbur serve`, and drives
  the panel from disconnected through the cold model load to a tool-using answer,
  target verification, and a tab-mismatch banner. Expect ~5-15 minutes (the cold
  model load is the long pole). It **skips** cleanly if the demo instance is
  unreachable (pick another from <https://im.dhis2.org/public/instances>).

Both scripts run `bun run build` first, so the `.output/chrome-mv3` bundle under
test is always current. Extensions are loaded headless via Chromium's new headless
mode (`channel: "chromium"`); export `HEADED=1` to force a visible window on a
machine that can't run headless extensions. The Playwright Chromium build must be
installed once (`bunx playwright install chromium`).

The live tier assumes `$HOME/.local/share/stabbur/library` contains
`lmstudio-community/gemma-4-12B-it-QAT-GGUF`, `uv`/`uvx` are on `PATH`, and this
repo is at its checkout root; it binds port 4599.

## Prompt catalog

`e2e/prompts/` holds a **verified catalog** of page-context prompts (extraction,
summarization, explanation, selection-grounded Q&A, transformation, reasoning) across
~8 real public sites, plus the batch harness that proves each one against a real
`gemma-4-12B`: capture the sites, reuse the panel's own `formatPageContext` to build the
context block, POST to `/api/chat`, and check each answer mechanically. `make
extension-prompts` runs it and regenerates the doc table; `bun run prompts:ui` drives a
few verified prompts through the real side panel end to end. The prompts themselves, with
copy-paste blocks and the live status table, live in
[`docs/guides/extension-prompts.md`](../docs/guides/extension-prompts.md).

## Shared-code integration

The panel imports the shared stabbur client and UI directly from `../frontend/src`
via the `@` alias (`@/lib/http`, `@/api`, `@/lib/utils`, and the shared
`@/components/*`). WXT hardcodes `@` -> the extension root through its own Vite
plugin, so `wxt.config.ts` appends a last-word plugin (in the
`vite:build:extendConfig` hook) that redirects `@` to `frontend/src`; the same
mapping is wired in `tsconfig.json` `paths` for the type-checker. The panel's
`style.css` imports the real theme (`frontend/src/index.css`) and points
Tailwind's `@source` scanning at both packages so utility classes used only in the
shared components survive tree-shaking. Dark mode follows the OS setting, applied
as `class="dark"` on `<html>` (the shared theme is `.dark`-driven).
