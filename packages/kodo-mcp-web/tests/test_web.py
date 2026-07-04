"""Behavior tests for the web-reader MCP server.

The SSRF guard and tool registration are tested hermetically (no browser, no network —
IP-literal and localhost lookups don't touch the network). The full render+extract path
is exercised by manual verification and the ``tools-web`` benchmark suite, which need
Chromium. The whole module is skipped when the optional ``web`` deps aren't installed.
"""

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
    for blocked in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "::1", "0.0.0.0"):
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


async def test_route_guard_blocks_private_allows_public() -> None:
    cache: dict[str, bool] = {}
    assert await app._route_is_blocked("http://127.0.0.1/x.js", cache) is True
    assert await app._route_is_blocked("ws://example.com/socket", cache) is True  # non-http scheme
    assert await app._route_is_blocked("http://93.184.216.34/x.css", cache) is False
