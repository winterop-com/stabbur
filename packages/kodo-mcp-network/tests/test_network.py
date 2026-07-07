"""Behavior tests for the network MCP server (in-memory client; external calls mocked)."""

from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from kodo_mcp_network import app, mcp


async def _call(name: str, **kw: Any) -> Any:
    async with Client(mcp) as client:
        return (await client.call_tool(name, kw)).data


def _ok(url: str, *, text: str = "", json_data: Any = None) -> httpx.Response:
    """A 200 response with its request set (so ``raise_for_status`` works)."""
    req = httpx.Request("GET", url)
    return (
        httpx.Response(200, json=json_data, request=req)
        if json_data is not None
        else httpx.Response(200, text=text, request=req)
    )


async def test_public_ip_returns_the_echoed_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.httpx, "get", lambda url, *a, **k: _ok(url, text="203.0.113.7\n"))
    assert await _call("public_ip") == "203.0.113.7"


async def test_public_ip_surfaces_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> httpx.Response:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(app.httpx, "get", boom)
    with pytest.raises(ToolError):
        await _call("public_ip")


async def test_location_parses_the_geo_service(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "success": True,
        "ip": "203.0.113.7",
        "city": "Oslo",
        "region": "Oslo",
        "country": "Norway",
        "latitude": 59.91,
        "longitude": 10.75,
        "timezone": {"id": "Europe/Oslo"},
    }
    monkeypatch.setattr(app.httpx, "get", lambda url, *a, **k: _ok(url, json_data=payload))
    loc = await _call("location")
    assert loc["city"] == "Oslo" and loc["timezone"] == "Europe/Oslo"
    assert loc["latitude"] == 59.91 and loc["country"] == "Norway"


async def test_location_reports_service_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app.httpx, "get", lambda url, *a, **k: _ok(url, json_data={"success": False, "message": "quota"})
    )
    with pytest.raises(ToolError):
        await _call("location")


async def test_tailscale_status_trims_self_and_peers(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = {
        "BackendState": "Running",
        "TailscaleIPs": ["100.64.0.1"],
        "MagicDNSSuffix": "tail1234.ts.net",
        "CurrentTailnet": {"Name": "example.ts.net"},
        "Self": {
            "HostName": "msai",
            "DNSName": "msai.tail1234.ts.net.",
            "TailscaleIPs": ["100.64.0.1"],
            "OS": "linux",
            "Online": True,
        },
        "Peer": {"k1": {"HostName": "laptop", "TailscaleIPs": ["100.64.0.2"], "OS": "macOS", "Online": False}},
    }
    monkeypatch.setattr(app, "_tailscale_json", lambda: fake)
    status = await _call("tailscale_status")
    assert status["backend_state"] == "Running" and status["tailnet"] == "example.ts.net"
    assert status["self"]["hostname"] == "msai" and status["self"]["dns_name"] == "msai.tail1234.ts.net"
    assert [p["hostname"] for p in status["peers"]] == ["laptop"]
    assert await _call("tailscale_ip") == ["100.64.0.1"]
    assert await _call("tailscale_host") == "msai.tail1234.ts.net"  # full MagicDNS FQDN, trailing dot stripped


async def test_tailscale_errors_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.shutil, "which", lambda _name: None)
    with pytest.raises(ToolError):
        await _call("tailscale_status")
