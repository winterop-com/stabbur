"""Behavior tests for the web-reader MCP server.

The SSRF guard and tool registration are tested hermetically (no browser, no network —
IP-literal and localhost lookups don't touch the network). The full render+extract path
is exercised by manual verification and the ``tools-web`` benchmark suite, which need
Chromium. The whole module is skipped when the optional ``web`` deps aren't installed.
"""

from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright")
pytest.importorskip("trafilatura")

from fastmcp import Client  # noqa: E402 - after importorskip so a base env skips cleanly
from kodo_mcp_web import app, mcp  # noqa: E402


async def test_server_exposes_read_url() -> None:
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
        assert "read_url" in names


def test_is_blocked_ip() -> None:
    for blocked in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "::1", "0.0.0.0", "100.64.0.1"):
        assert app._is_blocked_ip(blocked), blocked
    for ok in ("93.184.216.34", "8.8.8.8", "1.1.1.1"):
        assert not app._is_blocked_ip(ok), ok


def test_guard_rejects_non_http_schemes() -> None:
    for bad in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)", "data:text/html,hi"):
        with pytest.raises(ValueError):
            app._guard_url(bad)


def test_guard_blocks_private_and_loopback_hosts() -> None:
    # localhost resolves via /etc/hosts (no network); the rest are IP literals.
    for bad in (
        "http://127.0.0.1/",
        "http://localhost/",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
    ):
        with pytest.raises(ValueError):
            app._guard_url(bad)


def test_guard_allows_public_ip_literal() -> None:
    # An IP literal resolves without a network lookup; a public one must pass.
    app._guard_url("http://93.184.216.34/")


def test_guard_respects_allow_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.settings, "allow_private", True)
    app._guard_url("http://127.0.0.1:8080/")  # opted in -> allowed
    with pytest.raises(ValueError):  # scheme is still enforced even with allow_private
        app._guard_url("file:///etc/passwd")


def test_pin_host_blocks_private_resolution() -> None:
    # The pinned resolve vets every address up front — a host resolving privately never
    # gets a connection at all (the DNS-rebinding fix: resolve once, connect to the
    # vetted IP, never let the HTTP client re-resolve).
    with pytest.raises(ValueError, match="private|loopback|link-local"):
        app._pin_host(urlparse("http://127.0.0.1/"))
    with pytest.raises(ValueError, match="private|loopback|link-local"):
        app._pin_host(urlparse("http://localhost:8080/"))


def test_pin_host_pins_ip_and_keeps_hostname_for_tls() -> None:
    # The request URL is rewritten to the vetted IP; the Host header and SNI extension
    # keep the original hostname so virtual hosting + certificate verification still work.
    target, headers, ext = app._pin_host(urlparse("https://93.184.216.34:8443/x?q=1"))
    parsed = urlparse(target)
    assert parsed.hostname == "93.184.216.34" and parsed.port == 8443
    assert parsed.path == "/x" and parsed.query == "q=1"
    assert headers["Host"] == "93.184.216.34:8443"
    assert ext["sni_hostname"] == "93.184.216.34"


def test_needs_render_thin_vs_good() -> None:
    big_html = "x" * 100_000
    # Near-empty extraction (JS app-shell / failure) -> render.
    assert app._needs_render("", big_html) is True
    assert app._needs_render("hi", "small") is True
    # Tiny extract from a large page (a front page trafilatura pruned) -> render + visible text.
    assert app._needs_render("a consent banner, ~40 chars of junk only", big_html) is True
    # A genuine (short) article on a small page -> trust the extract, don't render.
    assert app._needs_render("A short but real article body. " * 5, "html" * 500) is False
    # A rich article extract -> trust it.
    assert app._needs_render("word " * 500, big_html) is False


async def test_route_guard_blocks_private_allows_public() -> None:
    cache: dict[str, bool] = {}
    assert await app._route_is_blocked("http://127.0.0.1/x.js", cache) is True
    assert await app._route_is_blocked("ws://example.com/socket", cache) is True  # non-http scheme
    assert await app._route_is_blocked("http://93.184.216.34/x.css", cache) is False
