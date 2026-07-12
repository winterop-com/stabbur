"""A FastMCP server that searches the web and returns titled results.

One tool, ``search``: given a query it returns a short numbered list of results — each a
title, URL, and snippet — for a model to then ``read_url`` (pairs with ``heim-mcp-web``).

Backends (``HEIM_SEARCH_BACKEND``, default ``auto``):

* ``duckduckgo`` — no API key; scrapes the DuckDuckGo HTML endpoint. The zero-config default.
* ``brave`` — the Brave Search API; needs ``HEIM_SEARCH_BRAVE_KEY``.
* ``exa`` — the Exa API; needs ``HEIM_SEARCH_EXA_KEY``.

``auto`` picks Brave or Exa when their key is set, else DuckDuckGo. A keyed backend selected
without its key returns a clear hint, not a crash.

Run standalone over stdio: ``heim-mcp-search`` (or ``python -m heim.mcp_servers.search``). Point heim
at it with ``heim mcp add search`` (or ``heim chat --mcp heim-mcp-search``).
"""

from __future__ import annotations

import asyncio
import html
import re
from urllib.parse import parse_qs, urlparse

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

mcp: FastMCP = FastMCP("heim-search")


class Settings(BaseSettings):
    """Runtime config, from ``HEIM_SEARCH_*`` env vars (all optional)."""

    model_config = SettingsConfigDict(env_prefix="HEIM_SEARCH_")

    backend: str = "auto"  # auto | duckduckgo | brave | exa
    brave_key: str = ""
    exa_key: str = ""
    max_results: int = 5
    timeout_seconds: float = 15.0
    user_agent: str = "Mozilla/5.0 (compatible; heim-mcp-search/0.1; +https://github.com/winterop-com/heim)"


settings = Settings()


class SearchResult(BaseModel):
    """One search hit."""

    title: str
    url: str
    snippet: str = ""


def resolve_backend() -> str:
    """The backend to use: the explicit setting, or auto-pick (Brave/Exa if keyed, else DDG)."""
    if settings.backend != "auto":
        return settings.backend
    if settings.brave_key:
        return "brave"
    if settings.exa_key:
        return "exa"
    return "duckduckgo"


# --- DuckDuckGo (no key) ---------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
_RESULT_A = re.compile(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S)


def _clean(fragment: str) -> str:
    """Strip HTML tags + unescape entities from a result fragment."""
    return html.unescape(_TAG.sub("", fragment)).strip()


def _real_url(href: str) -> str:
    """Resolve a DuckDuckGo result href to the destination URL.

    DDG wraps hits in a redirect (``//duckduckgo.com/l/?uddg=<encoded target>``); unwrap it.
    Protocol-relative hrefs get ``https:`` prepended; direct URLs pass through.
    """
    href = href.strip()
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return href


def parse_ddg_html(page: str, limit: int) -> list[SearchResult]:
    """Parse the DuckDuckGo HTML results page into results (pure; unit-tested with a fixture)."""
    titles = _RESULT_A.findall(page)
    snippets = _SNIPPET.findall(page)
    out: list[SearchResult] = []
    for i, (href, title) in enumerate(titles[:limit]):
        snippet = _clean(snippets[i]) if i < len(snippets) else ""
        out.append(SearchResult(title=_clean(title), url=_real_url(href), snippet=snippet))
    return out


async def _search_duckduckgo(query: str, limit: int) -> list[SearchResult]:
    async with httpx.AsyncClient(
        timeout=settings.timeout_seconds,
        headers={"User-Agent": settings.user_agent},
        follow_redirects=True,
    ) as client:
        resp = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
        resp.raise_for_status()
        return parse_ddg_html(resp.text, limit)


# --- Brave / Exa (keyed JSON APIs) -----------------------------------------


async def _search_brave(query: str, limit: int) -> list[SearchResult]:
    if not settings.brave_key:
        raise ValueError("the Brave backend needs HEIM_SEARCH_BRAVE_KEY (or use the default duckduckgo backend)")
    async with httpx.AsyncClient(
        timeout=settings.timeout_seconds,
        headers={"X-Subscription-Token": settings.brave_key, "Accept": "application/json"},
    ) as client:
        resp = await client.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": limit})
        resp.raise_for_status()
        hits = resp.json().get("web", {}).get("results", [])[:limit]
        return [
            SearchResult(
                title=_clean(h.get("title", "")), url=h.get("url", ""), snippet=_clean(h.get("description", ""))
            )
            for h in hits
        ]


async def _search_exa(query: str, limit: int) -> list[SearchResult]:
    if not settings.exa_key:
        raise ValueError("the Exa backend needs HEIM_SEARCH_EXA_KEY (or use the default duckduckgo backend)")
    async with httpx.AsyncClient(
        timeout=settings.timeout_seconds,
        headers={"x-api-key": settings.exa_key, "Content-Type": "application/json"},
    ) as client:
        resp = await client.post("https://api.exa.ai/search", json={"query": query, "numResults": limit})
        resp.raise_for_status()
        hits = resp.json().get("results", [])[:limit]
        return [
            SearchResult(title=_clean(h.get("title", "") or h.get("url", "")), url=h.get("url", ""), snippet="")
            for h in hits
        ]


_BACKENDS = {"duckduckgo": _search_duckduckgo, "brave": _search_brave, "exa": _search_exa}


@mcp.tool
async def search(query: str, max_results: int = 0) -> str:
    """Search the web and return a numbered list of results (title, URL, snippet).

    Use this to find pages, then read one with a fetch/read tool. ``max_results`` caps the
    number of hits (0 = the server default). Results come from DuckDuckGo by default, or the
    Brave/Exa API when a ``HEIM_SEARCH_*`` key is configured.
    """
    limit = max_results if max_results and max_results > 0 else settings.max_results
    backend = resolve_backend()
    fn = _BACKENDS.get(backend)
    if fn is None:
        raise ValueError(f"unknown HEIM_SEARCH_BACKEND {backend!r}; use one of: auto, duckduckgo, brave, exa")
    try:
        results = await fn(query, limit)
    except ValueError:
        raise  # config problems (missing key) already read clearly
    except Exception as exc:  # noqa: BLE001 - network/HTTP/parse; surface cleanly to the model
        raise ValueError(f"search failed via {backend}: {exc}") from exc
    if not results:
        return f"No results for {query!r}."
    blocks = [
        f"{i}. {r.title}\n   {r.url}" + (f"\n   {r.snippet}" if r.snippet else "") for i, r in enumerate(results, 1)
    ]
    return "\n\n".join(blocks)


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn).

    Swallow Ctrl-C / stream-closed shutdown noise so exiting is quiet.
    """
    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
