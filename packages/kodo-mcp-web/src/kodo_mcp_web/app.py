"""A FastMCP server that reads a web page and returns readable Markdown.

One tool, ``read_url``. It fetches the page cheaply first (a plain httpx GET) and extracts the
main content with trafilatura; if that's thin — a JavaScript-rendered page, or a front page /
listing whose content trafilatura prunes as boilerplate — it renders the page in a headless
browser (Playwright/Chromium) and, when the precise extract is still thin, returns the page's
**visible text** (what you'd read looking at it). Output is prefixed with the title + source URL.

Security (SSRF): only ``http(s)`` URLs are allowed, and any host that resolves to a
private / loopback / link-local / reserved address is refused. On the static path the
connection is **pinned** to the vetted IP (resolve once, connect to that address, with the
``Host`` header + TLS SNI/verification kept on the original hostname), so a DNS-rebinding
host can't pass the check and then serve the fetch from a private address. The browser path
re-vets every request (subresources, redirects) via interception, but Chromium resolves its
own connections — a rebinding TOCTOU window remains there, so the static path is the strong
guarantee. Set ``KODO_WEB_ALLOW_PRIVATE=1`` to intentionally read an internal/localhost
host. A per-fetch navigation timeout bounds hangs and the returned Markdown is
length-capped (``KODO_WEB_MAX_CHARS``).

Needs the browser binary: the ``web`` extra installs the Playwright *package*, but Chromium
is a separate ~150 MB download — run ``playwright install chromium`` once. A missing browser
yields an install hint, not a hang.

Run standalone over stdio: ``kodo-mcp-web`` (or ``python -m kodo_mcp_web``). Point kodo at it
with ``kodo mcp add web`` (or ``kodo chat --mcp kodo-mcp-web``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from functools import partial
from html import unescape
from typing import Literal
from urllib.parse import ParseResult, urljoin, urlparse

import httpx
import trafilatura
from fastmcp import FastMCP
from playwright.async_api import Browser, BrowserContext, Playwright, Route, async_playwright
from pydantic_settings import BaseSettings, SettingsConfigDict

mcp: FastMCP = FastMCP("kodo-web")


class Settings(BaseSettings):
    """Runtime config, from ``KODO_WEB_*`` env vars (all optional)."""

    model_config = SettingsConfigDict(env_prefix="KODO_WEB_")

    timeout_seconds: float = 20.0  # per-page navigation timeout
    settle_ms: int = 500  # brief wait after load for late client-side rendering
    max_chars: int = 20_000  # cap the Markdown handed back to the model's context
    static_first: bool = True  # try a plain httpx GET first; fall back to the browser if it's thin
    thin_chars: int = 500  # extract shorter than this on a large page = trafilatura pruned it -> render
    large_html_bytes: int = 30_000  # a page whose HTML exceeds this is content-rich (see thin_chars)
    max_redirects: int = 5  # cap redirects on the static path (each hop is SSRF-guarded)
    # Playwright wait strategy; a Literal so an invalid KODO_WEB_WAIT_UNTIL is rejected at startup.
    wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "load"
    allow_private: bool = False  # opt in to private/loopback hosts (internal servers)
    user_agent: str = "kodo-mcp-web/0.1 (+https://github.com/winterop-com/kodo)"


settings = Settings()


# --- SSRF guard ------------------------------------------------------------


def _is_blocked_ip(ip: str) -> bool:
    """Whether an IP is one we refuse to reach (private/loopback/link-local/etc)."""
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _resolve_blocked(host: str, port: int) -> str | None:
    """Return a reason string if ``host`` resolves to a blocked address, else ``None``."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return f"could not resolve host {host!r}: {exc}"
    for info in infos:
        ip = str(info[4][0])
        if _is_blocked_ip(ip):
            return f"{host!r} resolves to {ip} (private/loopback/link-local address)"
    return None


def _guard_url(url: str) -> None:
    """Reject non-http(s) URLs and hosts that resolve to a blocked address (SSRF).

    ``KODO_WEB_ALLOW_PRIVATE=1`` skips the address check (but the scheme is still enforced).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"only http(s) URLs are allowed, got {parsed.scheme or 'no'}-scheme URL {url!r}")
    host = parsed.hostname
    if not host:
        raise ValueError(f"no host in URL {url!r}")
    if settings.allow_private:
        return
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    reason = _resolve_blocked(host, port)
    if reason is not None:
        raise ValueError(f"refusing to fetch {url!r}: {reason}")


def _pin_host(parsed: ParseResult) -> tuple[str, dict[str, str], dict[str, str]]:
    """Resolve ``parsed``'s host once, vet every address, and pin the connection to one.

    Returns ``(request_url, headers, extensions)``: the URL rewritten to a vetted IP, a
    matching ``Host`` header, and httpcore's ``sni_hostname`` extension so TLS still
    handshakes and verifies the certificate against the original hostname. Connecting to
    the checked IP — instead of letting the HTTP client re-resolve — closes the
    DNS-rebinding TOCTOU between the SSRF check and the fetch.
    """
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve host {host!r}: {exc}") from exc
    ips = [str(info[4][0]) for info in infos]
    for ip in ips:
        if _is_blocked_ip(ip):
            raise ValueError(f"refusing to fetch: {host!r} resolves to {ip} (private/loopback/link-local address)")
    pinned = ips[0]
    netloc = f"[{pinned}]" if ":" in pinned else pinned
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    host_header = host if parsed.port is None else f"{host}:{parsed.port}"
    return parsed._replace(netloc=netloc).geturl(), {"Host": host_header}, {"sni_hostname": host}


async def _route_is_blocked(url: str, cache: dict[str, bool]) -> bool:
    """Whether the browser should abort a request to ``url`` (bad scheme or blocked host).

    ``cache`` memoizes host lookups for this page load so repeated subresource hosts don't
    re-resolve; DNS runs in a thread so it never blocks the event loop.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if parsed.scheme not in ("http", "https") or not host:
        return True
    if settings.allow_private:
        return False
    if host not in cache:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        loop = asyncio.get_running_loop()
        cache[host] = await loop.run_in_executor(None, lambda: _resolve_blocked(host, port) is not None)
    return cache[host]


async def _guard_route(route: Route, cache: dict[str, bool]) -> None:
    """Playwright request-interceptor: abort requests to blocked hosts, else continue."""
    if await _route_is_blocked(route.request.url, cache):
        await route.abort()
    else:
        await route.continue_()


# --- browser lifecycle (lazy, reused across calls) -------------------------

_playwright: Playwright | None = None
_browser: Browser | None = None
_browser_lock = asyncio.Lock()


async def _get_browser() -> Browser:
    """Launch Chromium once and reuse it; a missing browser binary yields an install hint."""
    global _playwright, _browser
    async with _browser_lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        _playwright = await async_playwright().start()
        try:
            _browser = await _playwright.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001 - most likely the browser isn't installed
            await _playwright.stop()
            _playwright = None
            raise ValueError(
                f"could not launch Chromium ({exc}). Install it once with `playwright install chromium`."
            ) from exc
        return _browser


# --- extraction + formatting -----------------------------------------------

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAGS_RE = re.compile(r"<[^>]+>")


def _title_from_html(html: str) -> str:
    """Pull the ``<title>`` text out of raw HTML (for the static path; browser uses page.title())."""
    match = _TITLE_RE.search(html)
    return unescape(_TAGS_RE.sub("", match.group(1))).strip() if match else ""


_BLANKS_RE = re.compile(r"\n{3,}")

# Extract shorter than this = basically nothing (a JS app-shell or an extraction failure).
_EMPTY_FLOOR = 50


def _extract_body(html: str, final_url: str) -> str:
    """Main content of a page as Markdown (trafilatura), stripped; empty if nothing extractable."""
    raw = trafilatura.extract(html, output_format="markdown", include_links=True, url=final_url)
    return (raw or "").strip()


def _needs_render(body: str, html: str) -> bool:
    """Whether trafilatura's extract is too thin to trust, so we should render + use visible text.

    Two cases: near-empty extraction (a JS app-shell, or a failure), or a tiny extract from a
    large page — a front page / listing whose content trafilatura pruned as boilerplate (e.g. a
    news homepage). Both mean the precise extractor didn't capture the page's real content.
    """
    if len(body) < _EMPTY_FLOOR:
        return True
    return len(body) < settings.thin_chars and len(html) > settings.large_html_bytes


def _collapse(text: str) -> str:
    """Tidy visible text: trim, and collapse runs of blank lines."""
    return _BLANKS_RE.sub("\n\n", text).strip()


def _format(body: str, final_url: str, title: str) -> str:
    """Assemble the final tool output: a title heading + source URL + capped Markdown body."""
    if len(body) > settings.max_chars:
        body = body[: settings.max_chars].rstrip() + "\n\n… [truncated]"
    header = f"# {title}\n" if title else ""
    return f"{header}<{final_url}>\n\n{body}"


# --- fetching: static (httpx) fast path, then browser (Playwright) fallback ---


async def _fetch_static(url: str) -> tuple[str, str, str]:
    """Fetch a page with a plain guarded httpx GET; return (final_url, html, title).

    Redirects are followed manually so every hop is SSRF-guarded, and each hop's connection
    is pinned to its vetted IP (:func:`_pin_host`) so rebinding DNS between check and fetch
    can't reach a private address. Non-text responses raise (the caller falls back to the
    browser). No JavaScript — cheap, for static/server-rendered pages.
    """
    async with httpx.AsyncClient(
        timeout=settings.timeout_seconds,
        headers={"User-Agent": settings.user_agent},
        follow_redirects=False,
    ) as client:
        current = url  # the logical URL (real hostname); requests go to the pinned-IP rewrite
        for _ in range(settings.max_redirects + 1):
            parsed = urlparse(current)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError(f"only http(s) URLs with a host are allowed, got {current!r}")
            if settings.allow_private:
                resp = await client.get(current)
            else:
                target, headers, extensions = _pin_host(parsed)
                resp = await client.get(target, headers=headers, extensions=extensions)
            if resp.is_redirect and "location" in resp.headers:
                # Join against the logical URL, not resp.url (which carries the pinned IP).
                current = urljoin(current, resp.headers["location"])
                continue
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if not any(kind in content_type for kind in ("html", "xml", "text")):
                raise ValueError(f"non-text content-type {content_type!r}")
            return current, resp.text, _title_from_html(resp.text)
        raise ValueError(f"too many redirects (> {settings.max_redirects})")


async def _fetch_browser(url: str) -> tuple[str, str, str, str]:
    """Render a page in headless Chromium; return (final_url, html, title, visible_text).

    ``visible_text`` is the rendered ``body`` text (what you'd read looking at the page) — a
    fallback for JS-hydrated pages whose content trafilatura can't extract from the HTML.
    """
    browser = await _get_browser()
    context: BrowserContext = await browser.new_context(user_agent=settings.user_agent)
    cache: dict[str, bool] = {}
    try:
        await context.route("**/*", partial(_guard_route, cache=cache))
        page = await context.new_page()
        try:
            await page.goto(url, wait_until=settings.wait_until, timeout=settings.timeout_seconds * 1000)
            if settings.settle_ms > 0:
                await page.wait_for_timeout(settings.settle_ms)
        except Exception as exc:  # noqa: BLE001 - timeouts, navigation errors, aborted (blocked) requests
            raise ValueError(f"failed to load {url!r}: {exc}") from exc
        final_url = str(page.url)
        _guard_url(final_url)  # a client-side redirect may have moved us off the vetted host
        return final_url, await page.content(), (await page.title()).strip(), await page.inner_text("body")
    finally:
        await context.close()


# --- the tool --------------------------------------------------------------


@mcp.tool
async def read_url(url: str) -> str:
    """Read a web page and return its main content as Markdown.

    Fetches the page — a cheap static HTTP GET first, falling back to a headless browser when
    the static extraction is thin (a JavaScript-rendered page) — strips boilerplate (nav, ads,
    footers), and returns Markdown prefixed with the page title and source URL. Only ``http(s)``
    URLs to public hosts are allowed; long pages are truncated with a note. Pass a full URL
    including the scheme, e.g. ``https://example.com/article``.
    """
    _guard_url(url)

    # Fast path: a plain HTTP GET is enough for static/server-rendered article pages and skips
    # the browser entirely. If the extract is thin (a JS-rendered SPA or a listing page whose
    # content trafilatura pruned) or the fetch fails, fall back to the browser below.
    if settings.static_first:
        try:
            final_url, html, title = await _fetch_static(url)
            body = _extract_body(html, final_url)
            if not _needs_render(body, html):
                return _format(body, final_url, title)
        except Exception:  # noqa: BLE001 - any static failure just means we try the browser
            pass

    final_url, html, title, visible_text = await _fetch_browser(url)
    body = _extract_body(html, final_url)
    if not _needs_render(body, html):
        return _format(body, final_url, title)

    # Rendered, but trafilatura's precise extract is still thin (a JS-hydrated front page /
    # listing). Return the page's visible text — what you'd get reading it in a browser
    # directly — when that's richer than the precise extract.
    text = _collapse(visible_text)
    if len(text) > len(body):
        return _format(text, final_url, title)
    if body:
        return _format(body, final_url, title)
    raise ValueError(f"no readable content extracted from {url!r} (the page may be empty, blocked, or require login)")


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn).

    Swallow Ctrl-C / stream-closed shutdown noise so exiting is quiet.
    """
    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
