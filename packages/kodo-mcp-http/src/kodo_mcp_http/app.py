"""A FastMCP server exposing an SSRF-guarded, allowlisted HTTP fetch tool, over stdio.

Two tools, ``http_get`` and ``http_head``: fetch an allowlisted URL and return its status, final
URL, content-type, and (for GET) the body text — a narrow, safe "call this API / read this
endpoint" primitive for a local assistant, without the open-ended fetch surface a general HTTP
client would give a model.

Safe by default (fail-closed): the allowlist is **empty by default**, so every request is refused
with a clear "no hosts allowlisted" message until you opt hosts in via ``KODO_MCP_HTTP_ALLOWLIST``.
A host matches an allowlist entry exactly or as a subdomain (``api.example.com`` matches
``example.com``).

Security (SSRF): only ``http(s)`` URLs are allowed; the host must be allowlisted; and the host is
resolved once and every resolved IP vetted — any private / loopback / link-local / reserved /
CGNAT address (``not is_global``) is refused. The connection is then **pinned** to the vetted IP
(the request goes to that address, with the ``Host`` header + TLS SNI/verification kept on the
original hostname), so a DNS-rebinding host can't pass the check and then serve the fetch from a
private address. Redirects are followed manually so **every hop is re-vetted** (allowlist + IP);
the auto-follow is disabled. Set ``KODO_MCP_HTTP_ALLOW_PRIVATE=1`` to intentionally reach an
internal/localhost host (still allowlist-gated).

Residual caveat: the pinned-IP static path closes the DNS-rebinding TOCTOU for the connection we
open. A fully airtight guarantee against rebinding on a *streaming* connection is hard (the same
caveat ``kodo-mcp-web`` notes for its browser path) — we resolve, vet, and pin once, which is the
strong, practical guarantee.

Run standalone over stdio: ``kodo-mcp-http`` (or ``python -m kodo_mcp_http``). Point kodo at it
with ``kodo mcp add http`` (or ``kodo chat --mcp kodo-mcp-http``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import ParseResult, urljoin, urlparse

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

mcp: FastMCP = FastMCP("kodo-http")


class Settings(BaseSettings):
    """Runtime config, from ``KODO_MCP_HTTP_*`` env vars (all optional).

    ``allowlist`` is empty by default so the tool is fail-closed (deny all) until hosts are opted
    in. Comma-separated in the env var, e.g. ``KODO_MCP_HTTP_ALLOWLIST=example.com,api.acme.io``.
    """

    model_config = SettingsConfigDict(env_prefix="KODO_MCP_HTTP_")

    allowlist: list[str] = []  # allowed hostnames/domains; EMPTY = deny all (safe default)
    max_bytes: int = 1_000_000  # cap the response body handed back (streamed + aborted past this)
    timeout_s: float = 15.0  # per-request timeout (connect + read)
    allow_private: bool = False  # opt in to private/loopback hosts (skips the IP vet + pin)
    max_redirects: int = 5  # cap redirects; every hop is re-vetted (allowlist + IP)
    user_agent: str = "kodo-mcp-http/0.1 (+https://github.com/winterop-com/kodo)"


settings = Settings()

# Test hook: an httpx transport to route requests through (a MockTransport in tests). None in
# production, so httpx uses its real network transport.
_transport: httpx.AsyncBaseTransport | None = None


class HttpToolError(Exception):
    """Internal signal for a blocked/invalid request; caught and returned as a structured error."""


class HttpResponse(BaseModel):
    """Structured result of an HTTP fetch (never an exception across the tool boundary).

    On success ``ok`` is True and ``status`` / ``url`` / ``content_type`` are set (plus ``text``
    for GET). On refusal/timeout/oversize-with-abort ``ok`` is False and ``error`` explains why.
    """

    ok: bool
    status: int | None = None
    url: str | None = None  # final URL after any redirects
    content_type: str | None = None
    content_length: int | None = None  # from the response header, if present
    text: str | None = None  # GET body, decoded; None for HEAD
    truncated: bool = False  # True when the body was capped at max_bytes
    error: str | None = None


# --- SSRF guard ------------------------------------------------------------


def _is_blocked_ip(ip: str) -> bool:
    """Whether an IP is one we refuse to reach (private/loopback/link-local/CGNAT/etc).

    ``not is_global`` subsumes private/loopback/link-local/reserved/unspecified AND the shared
    address space (CGNAT ``100.64.0.0/10``), which ``is_private`` alone misses. Multicast is
    blocked explicitly — parts of it (e.g. ``224.0.1.0/24``) count as global.
    """
    addr = ipaddress.ip_address(ip)
    return addr.is_multicast or not addr.is_global


def _host_allowed(host: str) -> bool:
    """Whether ``host`` matches the allowlist (exact host or a subdomain of an entry).

    Empty allowlist always returns False (fail-closed). Matching is case-insensitive and ignores a
    trailing dot; ``api.example.com`` matches an entry of ``example.com`` but ``notexample.com``
    does not (subdomain boundary is enforced).
    """
    h = host.lower().rstrip(".")
    for raw in settings.allowlist:
        entry = raw.strip().lower().rstrip(".")
        if entry and (h == entry or h.endswith("." + entry)):
            return True
    return False


def _pin_host(parsed: ParseResult) -> tuple[str, dict[str, str], dict[str, str]]:
    """Resolve ``parsed``'s host once, vet every address, and pin the connection to one.

    Returns ``(request_url, headers, extensions)``: the URL rewritten to a vetted IP, a matching
    ``Host`` header, and httpcore's ``sni_hostname`` extension so TLS still handshakes and verifies
    the certificate against the original hostname. Connecting to the checked IP — instead of
    letting the HTTP client re-resolve — closes the DNS-rebinding TOCTOU between the SSRF check and
    the fetch. Raises :class:`HttpToolError` if the host won't resolve or resolves to a blocked IP.
    """
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise HttpToolError(f"could not resolve host {host!r}: {exc}") from exc
    ips = [str(info[4][0]) for info in infos]
    for ip in ips:
        if _is_blocked_ip(ip):
            raise HttpToolError(f"refusing to fetch: {host!r} resolves to {ip} (private/loopback/link-local address)")
    pinned = ips[0]
    netloc = f"[{pinned}]" if ":" in pinned else pinned
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    host_header = host if parsed.port is None else f"{host}:{parsed.port}"
    return parsed._replace(netloc=netloc).geturl(), {"Host": host_header}, {"sni_hostname": host}


def _vet_hop(url: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """Vet one request URL (scheme + allowlist + resolved IP) and return the pinned request target.

    Returns ``(request_url, extra_headers, extensions)`` to hand to httpx. With ``allow_private``
    the IP vet + pin are skipped (the URL is used as-is), but scheme and allowlist are still
    enforced. Raises :class:`HttpToolError` on any failure.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HttpToolError(f"only http(s) URLs are allowed, got {parsed.scheme or 'no'}-scheme URL {url!r}")
    host = parsed.hostname
    if not host:
        raise HttpToolError(f"no host in URL {url!r}")
    if not settings.allowlist:
        raise HttpToolError("no hosts allowlisted — set KODO_MCP_HTTP_ALLOWLIST to opt hosts in (deny-all by default)")
    if not _host_allowed(host):
        raise HttpToolError(f"host {host!r} is not in the allowlist {settings.allowlist!r}")
    if settings.allow_private:
        return url, {}, {}
    return _pin_host(parsed)


# --- fetching --------------------------------------------------------------


async def _read_capped(resp: httpx.Response) -> tuple[str, bool]:
    """Stream the body, capping at ``max_bytes``; return ``(text, truncated)``.

    Aborts the stream as soon as the cap is exceeded (never buffers the whole oversized body).
    Decodes with the response's declared charset, falling back to UTF-8 with replacement so binary
    or mislabeled bodies never raise.
    """
    chunks: list[bytes] = []
    total = 0
    truncated = False
    async for chunk in resp.aiter_bytes():
        if total + len(chunk) > settings.max_bytes:
            chunks.append(chunk[: settings.max_bytes - total])
            truncated = True
            break
        chunks.append(chunk)
        total += len(chunk)
    data = b"".join(chunks)
    encoding = resp.charset_encoding or "utf-8"
    text = data.decode(encoding, errors="replace")
    if truncated:
        text += "\n\n… [truncated]"
    return text, truncated


async def _fetch(method: str, url: str, headers: dict[str, str] | None, *, read_body: bool) -> HttpResponse:
    """Fetch ``url`` with SSRF guarding and manual, re-vetted redirects; return a structured result.

    Every hop (initial + each redirect) is vetted (scheme, allowlist, resolved IP) and pinned to
    its vetted IP. Any block, resolution failure, timeout, or transport error is returned as an
    ``HttpResponse(ok=False, error=...)`` rather than raised, so the model always gets a usable
    answer.
    """
    base_headers = {"User-Agent": settings.user_agent, **(headers or {})}
    try:
        async with httpx.AsyncClient(
            timeout=settings.timeout_s,
            follow_redirects=False,
            transport=_transport,
        ) as client:
            current = url  # the logical URL (real hostname); requests go to the pinned-IP rewrite
            for _ in range(settings.max_redirects + 1):
                target, pin_headers, extensions = _vet_hop(current)
                req_headers = {**base_headers, **pin_headers}
                async with client.stream(method, target, headers=req_headers, extensions=extensions) as resp:
                    if resp.is_redirect and "location" in resp.headers:
                        # Join against the logical URL, not resp.url (which carries the pinned IP).
                        current = urljoin(current, resp.headers["location"])
                        continue
                    content_type = resp.headers.get("content-type")
                    length_header = resp.headers.get("content-length")
                    content_length = int(length_header) if length_header and length_header.isdigit() else None
                    text: str | None = None
                    truncated = False
                    if read_body:
                        text, truncated = await _read_capped(resp)
                    return HttpResponse(
                        ok=True,
                        status=resp.status_code,
                        url=current,
                        content_type=content_type,
                        content_length=content_length,
                        text=text,
                        truncated=truncated,
                    )
            return HttpResponse(ok=False, error=f"too many redirects (> {settings.max_redirects})")
    except HttpToolError as exc:
        return HttpResponse(ok=False, error=str(exc))
    except httpx.TimeoutException:
        return HttpResponse(ok=False, error=f"request to {url!r} timed out after {settings.timeout_s}s")
    except httpx.HTTPError as exc:
        return HttpResponse(ok=False, error=f"request to {url!r} failed: {exc}")


# --- the tools -------------------------------------------------------------


@mcp.tool
async def http_get(url: str, headers: dict[str, str] | None = None) -> HttpResponse:
    """GET an allowlisted URL and return its status, final URL, content-type, and body text.

    Only ``http(s)`` URLs to **allowlisted** hosts are fetched (the allowlist is empty by default,
    so nothing is reachable until you configure ``KODO_MCP_HTTP_ALLOWLIST``). Private/loopback/
    internal addresses are refused, and redirects are re-checked at every hop. The body is capped
    (``KODO_MCP_HTTP_MAX_BYTES``) and marked ``truncated`` if it was cut. Blocks, timeouts, and
    oversize aborts come back as ``ok=false`` with an ``error`` — never an exception. Pass a full
    URL including the scheme, e.g. ``https://api.example.com/v1/thing``; optional ``headers`` are
    merged into the request.
    """
    return await _fetch("GET", url, headers, read_body=True)


@mcp.tool
async def http_head(url: str, headers: dict[str, str] | None = None) -> HttpResponse:
    """HEAD an allowlisted URL: fetch its status, final URL, and content metadata without the body.

    Same allowlist + SSRF guarding as ``http_get`` (empty allowlist denies; private hosts refused;
    redirects re-vetted), but returns headers only — ``status``, ``content_type``,
    ``content_length`` — with no ``text``. Useful to check existence, type, or size before a GET.
    """
    return await _fetch("HEAD", url, headers, read_body=False)


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn). Swallow shutdown noise."""
    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
