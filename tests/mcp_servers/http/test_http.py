"""Behavior tests for the http-fetch MCP server.

The SSRF guard is tested hermetically (IP-literal and localhost lookups don't touch the network).
The fetch path is exercised with an httpx ``MockTransport`` — no real network — by injecting it via
``app._transport`` and opting into ``allow_private`` (to skip the real-DNS pin) where a request is
meant to reach the mock.
"""

import httpx
import pytest
from fastmcp import Client

from stabbur.mcp_servers.http import app, mcp


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test from known settings and no injected transport."""
    monkeypatch.setattr(app, "_transport", None)
    monkeypatch.setattr(app.settings, "allowlist", [])
    monkeypatch.setattr(app.settings, "allow_private", False)
    monkeypatch.setattr(app.settings, "max_bytes", 1_000_000)
    monkeypatch.setattr(app.settings, "max_redirects", 5)


def _mock(handler: object) -> httpx.MockTransport:
    return httpx.MockTransport(handler)  # type: ignore[arg-type]


# --- registration ----------------------------------------------------------


async def test_server_exposes_tools() -> None:
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
        assert {"http_get", "http_head"} <= names


# --- SSRF guard units ------------------------------------------------------


def test_is_blocked_ip() -> None:
    for blocked in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "::1", "0.0.0.0", "100.64.0.1"):
        assert app._is_blocked_ip(blocked), blocked
    for ok in ("93.184.216.34", "8.8.8.8", "1.1.1.1"):
        assert not app._is_blocked_ip(ok), ok


def test_host_allowed_exact_and_subdomain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.settings, "allowlist", ["example.com", "acme.io"])
    assert app._host_allowed("example.com")
    assert app._host_allowed("api.example.com")  # subdomain matches
    assert app._host_allowed("EXAMPLE.com")  # case-insensitive
    assert not app._host_allowed("notexample.com")  # not a subdomain boundary
    assert not app._host_allowed("evil.com")


def test_host_allowed_empty_denies() -> None:
    assert app.settings.allowlist == []
    assert not app._host_allowed("example.com")


def test_pin_host_blocks_private_resolution() -> None:
    from urllib.parse import urlparse  # noqa: PLC0415

    with pytest.raises(app.HttpToolError, match="private|loopback|link-local"):
        app._pin_host(urlparse("http://127.0.0.1/"))
    with pytest.raises(app.HttpToolError, match="private|loopback|link-local"):
        app._pin_host(urlparse("http://localhost:8080/"))


def test_pin_host_pins_ip_and_keeps_hostname_for_tls() -> None:
    from urllib.parse import urlparse  # noqa: PLC0415

    target, headers, ext = app._pin_host(urlparse("https://93.184.216.34:8443/x?q=1"))
    parsed = urlparse(target)
    assert parsed.hostname == "93.184.216.34" and parsed.port == 8443
    assert parsed.path == "/x" and parsed.query == "q=1"
    assert headers["Host"] == "93.184.216.34:8443"
    assert ext["sni_hostname"] == "93.184.216.34"


# --- fetch behavior --------------------------------------------------------


async def test_empty_allowlist_denies() -> None:
    # No allowlist configured (the default) -> fail-closed, no network touched.
    result = await app._fetch("GET", "http://example.com/", None, read_body=True)
    assert result.ok is False
    assert "no hosts allowlisted" in (result.error or "")


async def test_non_allowlisted_host_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.settings, "allowlist", ["example.com"])
    result = await app._fetch("GET", "http://evil.com/", None, read_body=True)
    assert result.ok is False
    assert "not in the allowlist" in (result.error or "")


async def test_allowlisted_host_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "example.com"
        return httpx.Response(200, text="hello world", headers={"content-type": "text/plain"})

    monkeypatch.setattr(app.settings, "allowlist", ["example.com"])
    monkeypatch.setattr(app.settings, "allow_private", True)  # skip real DNS pin; MockTransport answers
    monkeypatch.setattr(app, "_transport", _mock(handler))

    result = await app._fetch("GET", "http://example.com/thing", None, read_body=True)
    assert result.ok is True
    assert result.status == 200
    assert result.content_type == "text/plain"
    assert result.text == "hello world"
    assert result.truncated is False


async def test_oversized_response_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100, headers={"content-type": "text/plain"})

    monkeypatch.setattr(app.settings, "allowlist", ["example.com"])
    monkeypatch.setattr(app.settings, "allow_private", True)
    monkeypatch.setattr(app.settings, "max_bytes", 10)
    monkeypatch.setattr(app, "_transport", _mock(handler))

    result = await app._fetch("GET", "http://example.com/big", None, read_body=True)
    assert result.ok is True
    assert result.truncated is True
    assert result.text is not None and result.text.startswith("x" * 10)
    assert "[truncated]" in result.text


async def test_private_ip_blocked_when_not_allow_private(monkeypatch: pytest.MonkeyPatch) -> None:
    # localhost is allowlisted (so it passes the allowlist stage) but resolves to a loopback IP,
    # which the pin step refuses because allow_private is False.
    monkeypatch.setattr(app.settings, "allowlist", ["localhost"])
    result = await app._fetch("GET", "http://localhost:8080/", None, read_body=True)
    assert result.ok is False
    assert "private/loopback/link-local" in (result.error or "")


async def test_non_http_scheme_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.settings, "allowlist", ["example.com"])
    result = await app._fetch("GET", "file:///etc/passwd", None, read_body=True)
    assert result.ok is False
    assert "http(s)" in (result.error or "")


async def test_head_returns_metadata_without_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        return httpx.Response(200, headers={"content-type": "application/json", "content-length": "42"})

    monkeypatch.setattr(app.settings, "allowlist", ["example.com"])
    monkeypatch.setattr(app.settings, "allow_private", True)
    monkeypatch.setattr(app, "_transport", _mock(handler))

    result = await app._fetch("HEAD", "http://example.com/x", None, read_body=False)
    assert result.ok is True
    assert result.status == 200
    assert result.content_type == "application/json"
    assert result.content_length == 42
    assert result.text is None


async def test_redirect_to_non_allowlisted_host_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://evil.com/"})
        raise AssertionError("must not follow the redirect to evil.com")

    monkeypatch.setattr(app.settings, "allowlist", ["example.com"])
    monkeypatch.setattr(app.settings, "allow_private", True)
    monkeypatch.setattr(app, "_transport", _mock(handler))

    result = await app._fetch("GET", "http://example.com/", None, read_body=True)
    assert result.ok is False
    assert "not in the allowlist" in (result.error or "")


async def test_redirect_to_allowlisted_host_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://example.com/end"})
        return httpx.Response(200, text="arrived", headers={"content-type": "text/plain"})

    monkeypatch.setattr(app.settings, "allowlist", ["example.com"])
    monkeypatch.setattr(app.settings, "allow_private", True)
    monkeypatch.setattr(app, "_transport", _mock(handler))

    result = await app._fetch("GET", "http://example.com/start", None, read_body=True)
    assert result.ok is True
    assert result.text == "arrived"
    assert result.url == "http://example.com/end"
