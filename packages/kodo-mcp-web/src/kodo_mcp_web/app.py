"""A FastMCP server that reads a web page in a headless browser and returns Markdown.

One tool, ``read_url``: it renders the page with Playwright (Chromium) — so
JavaScript-rendered pages work — then extracts the main content with trafilatura and
returns Markdown (boilerplate like nav/ads removed), prefixed with the title + source URL.

Security (SSRF): only ``http(s)`` URLs are allowed, and any host that resolves to a
private / loopback / link-local / reserved address is refused — checked for the top-level
URL *and* enforced on every request the browser makes (subresources, redirects) via request
interception, so a public page can't pivot to an internal address. Set
``KODO_WEB_ALLOW_PRIVATE=1`` to intentionally read an internal/localhost host. A per-fetch
navigation timeout bounds hangs and the returned Markdown is length-capped
(``KODO_WEB_MAX_CHARS``).

Needs the browser binary: the ``web`` extra installs the Playwright *package*, but Chromium
is a separate ~150 MB download — run ``playwright install chromium`` once. A missing browser
yields an install hint, not a hang.

Run standalone over stdio: ``kodo-mcp-web`` (or ``python -m kodo_mcp_web``). Point kodo at it
with ``kodo mcp add web`` (or ``kodo chat --mcp kodo-mcp-web``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from functools import partial
from typing import Literal
from urllib.parse import urlparse

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


# --- the tool --------------------------------------------------------------


@mcp.tool
async def read_url(url: str) -> str:
    """Read a web page and return its main content as Markdown.

    Renders the page in a headless browser (so JavaScript-rendered pages work), strips
    boilerplate (navigation, ads, footers), and returns Markdown prefixed with the page
    title and source URL. Only ``http(s)`` URLs to public hosts are allowed; long pages are
    truncated with a note. Pass a full URL including the scheme, e.g.
    ``https://example.com/article``.
    """
    _guard_url(url)
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
        html: str = await page.content()
        title: str = (await page.title()).strip()
    finally:
        await context.close()

    raw_md = trafilatura.extract(html, output_format="markdown", include_links=True, url=final_url)
    md: str = (raw_md or "").strip()
    if not md:
        raise ValueError(
            f"no readable content extracted from {url!r} (the page may be empty, blocked, or not an article)"
        )
    if len(md) > settings.max_chars:
        md = md[: settings.max_chars].rstrip() + "\n\n… [truncated]"
    header = f"# {title}\n" if title else ""
    return f"{header}<{final_url}>\n\n{md}"


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn).

    Swallow Ctrl-C / stream-closed shutdown noise so exiting is quiet.
    """
    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
