# kodo-mcp-web

An [MCP](https://modelcontextprotocol.io) server that **reads a web page and returns
readable Markdown**. It tries a cheap static HTTP GET first (httpx) and only falls back to
a **headless browser** (Playwright/Chromium) when the static extraction is thin — a
JavaScript-rendered page — so simple/static pages skip the browser entirely. Either way the
main content is extracted with [trafilatura](https://trafilatura.readthedocs.io)
(nav/ads/boilerplate removed). Kodo is the MCP *client*; this is one of the servers it can
spawn so a model can ground itself on a page ("read this URL").

One tool:

- **`read_url(url)`** → the page's main content as Markdown, prefixed with the title
  and source URL. Long pages are truncated with a note.

## Optional — needs a browser

Unlike the stdlib servers (`datetime`, `utils`), this one is **not bundled** with kodo
by default: Playwright + Chromium are heavy. Install it as an extra, then fetch the
browser binary once:

```
uv sync --extra web          # or: make install-web (does both steps)
playwright install chromium  # ~150 MB browser download, one time
```

Then point kodo at it:

```
kodo mcp add web                   # add it to ./kodo.toml
kodo-mcp-web                       # or run it standalone over stdio
kodo chat --mcp kodo-mcp-web       # kodo spawns it and exposes read_url
```

A missing browser yields an install hint, not a hang.

## Security (SSRF)

Only `http(s)` URLs are allowed, and any host resolving to a private / loopback /
link-local / reserved address is refused — for the top-level URL **and** every request
the browser makes (subresources, redirects), so a public page can't pivot to an internal
address. Read an internal/localhost host on purpose with `KODO_WEB_ALLOW_PRIVATE=1`.

## Config (`KODO_WEB_*`)

| Var | Default | Meaning |
| --- | --- | --- |
| `KODO_WEB_STATIC_FIRST` | `true` | Try a plain httpx GET first; fall back to the browser if thin. |
| `KODO_WEB_MIN_CHARS` | `100` | Static extraction shorter than this retries with the browser. |
| `KODO_WEB_TIMEOUT_SECONDS` | `20` | Per-page navigation timeout. |
| `KODO_WEB_SETTLE_MS` | `500` | Extra wait after load for late client-side rendering. |
| `KODO_WEB_MAX_CHARS` | `20000` | Cap on the returned Markdown. |
| `KODO_WEB_WAIT_UNTIL` | `load` | Playwright wait strategy (`load` / `domcontentloaded` / `networkidle` / `commit`). |
| `KODO_WEB_ALLOW_PRIVATE` | `false` | Allow private/loopback hosts (internal servers). |
| `KODO_WEB_USER_AGENT` | `kodo-mcp-web/…` | User-Agent sent to sites. |

## Package shape

This follows the same workspace-member layout as `kodo-mcp-datetime` (see that package's
README for the template): `src/kodo_mcp_web/{__init__,__main__,app,plugin}.py` + `tests/`.
The difference is the optional heavy deps, so it's wired behind the `web` extra rather than
bundled into the base install.
