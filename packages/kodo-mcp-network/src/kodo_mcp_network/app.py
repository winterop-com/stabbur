"""A FastMCP server exposing this machine's network identity, over stdio.

Answers the "about my machine's network" questions a local assistant otherwise can't:

* **public internet** — ``public_ip`` (the IP the internet sees) and ``location`` (approximate
  city/coordinates geolocated from that IP; enough to seed a weather forecast or answer "where
  am I"). Both query a small external service, so they're a mild privacy tradeoff — the same as
  any weather app. Nothing is stored.
* **tailnet** — ``tailscale_ip`` and ``tailscale_status`` read the local Tailscale CLI to report
  this node's tailnet address and the peer devices on it. No network beyond the tailnet; requires
  Tailscale to be installed (a clear error otherwise).

Run standalone over stdio: ``kodo-mcp-network`` (or ``python -m kodo_mcp_network``). Point kodo at
it with ``kodo chat --mcp kodo-mcp-network`` (it's a bundled base server, added by default).
"""

import json
import shutil
import subprocess
from typing import Any

import httpx
from fastmcp import FastMCP

mcp: FastMCP = FastMCP("kodo-network")

# Public HTTP calls are best-effort and shouldn't hang the agent loop; the local tailscale CLI is
# fast but capped too so a wedged daemon can't stall a chat.
_HTTP_TIMEOUT = 8.0
_CLI_TIMEOUT = 10.0


# --- public internet -------------------------------------------------------


@mcp.tool
def public_ip() -> str:
    """This machine's public IP address, as seen from the internet.

    Queries an external echo service (api.ipify.org). Behind NAT/VPN this is the exit IP, not a
    LAN or Tailscale address (use ``tailscale_ip`` for the tailnet address).
    """
    try:
        resp = httpx.get("https://api.ipify.org", timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"couldn't determine the public IP: {exc}") from exc
    return resp.text.strip()


@mcp.tool
def location() -> dict[str, Any]:
    """Approximate location of this machine, geolocated from its public IP.

    Returns ``ip``, ``city``, ``region``, ``country``, ``latitude``, ``longitude``, and ``timezone``
    (IANA). City-level and best-effort — good enough to answer "where am I" or to feed coordinates
    to a weather tool, not a precise position. Queries an external geolocation service (ipwho.is).
    """
    try:
        resp = httpx.get("https://ipwho.is/", timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"couldn't geolocate this machine: {exc}") from exc
    if not data.get("success", False):
        raise RuntimeError(f"geolocation failed: {data.get('message', 'unknown error')}")
    tz = data.get("timezone")
    return {
        "ip": data.get("ip"),
        "city": data.get("city"),
        "region": data.get("region"),
        "country": data.get("country"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": tz.get("id") if isinstance(tz, dict) else tz,
    }


# --- tailnet (Tailscale) ---------------------------------------------------


def _tailscale_json() -> dict[str, Any]:
    """Parse ``tailscale status --json`` (the local daemon), or raise a clear error."""
    exe = shutil.which("tailscale")
    if exe is None:
        raise RuntimeError("Tailscale is not installed — get it at https://tailscale.com/download.")
    try:
        proc = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=_CLI_TIMEOUT)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"couldn't run tailscale: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"tailscale status failed: {proc.stderr.strip()[:300]}")
    try:
        data: dict[str, Any] = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"tailscale returned unreadable output: {exc}") from exc
    return data


def _node(node: dict[str, Any]) -> dict[str, Any]:
    """Trim a Tailscale node (self or peer) to the fields worth reporting."""
    return {
        "hostname": node.get("HostName"),
        "dns_name": (node.get("DNSName") or "").rstrip("."),
        "ips": node.get("TailscaleIPs") or [],
        "os": node.get("OS"),
        "online": node.get("Online"),
        "exit_node": node.get("ExitNode"),
    }


@mcp.tool
def tailscale_ip() -> list[str]:
    """This machine's Tailscale (tailnet) IP addresses — IPv4 + IPv6, or empty if not connected."""
    data = _tailscale_json()
    return data.get("TailscaleIPs") or (data.get("Self") or {}).get("TailscaleIPs") or []


@mcp.tool
def tailscale_host() -> str:
    """This machine's full Tailscale (MagicDNS) hostname, e.g. ``msai.tail1234.ts.net``.

    The FQDN you can reach this node at on the tailnet — the short hostname plus the tailnet's
    MagicDNS suffix. Prefer this over the bare hostname when asked for the "Tailscale host". Empty
    if Tailscale isn't connected.
    """
    self_node = _tailscale_json().get("Self") or {}
    return (self_node.get("DNSName") or "").rstrip(".")


@mcp.tool
def tailscale_status() -> dict[str, Any]:
    """This machine's Tailscale status: its own node plus the peer devices on the tailnet.

    Returns ``backend_state`` (``Running`` / ``Stopped`` / ``NeedsLogin``), the ``tailnet`` name,
    the MagicDNS suffix, ``self`` (hostname, DNS name, tailnet IPs, OS, online, exit-node), and a
    ``peers`` list of the other devices. Reads the local ``tailscale`` CLI; requires Tailscale.
    """
    data = _tailscale_json()
    return {
        "backend_state": data.get("BackendState"),
        "tailnet": (data.get("CurrentTailnet") or {}).get("Name"),
        "magic_dns_suffix": data.get("MagicDNSSuffix"),
        "self": _node(data.get("Self") or {}),
        "peers": [_node(peer) for peer in (data.get("Peer") or {}).values()],
    }


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn). Swallow shutdown noise."""
    import asyncio  # noqa: PLC0415

    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
