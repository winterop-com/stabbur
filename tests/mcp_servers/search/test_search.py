"""Behavior tests for the web-search MCP server (hermetic — no network)."""

from typing import Any

import pytest
from fastmcp import Client

from heim.mcp_servers.search import app, mcp

# A trimmed but realistic DuckDuckGo HTML results page: two hits, one wrapped in DDG's /l/
# redirect (uddg=), one a direct URL, with entities + inner tags to strip.
_DDG_FIXTURE = """
<div class="results">
  <div class="result results_links results_links_deep web-result">
    <a class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=abc"
       >Example &amp; <b>A</b></a>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">The <b>first</b> result snippet.</a>
  </div>
  <div class="result results_links results_links_deep web-result">
    <a class="result__a" href="https://python.org/">Python.org</a>
    <a class="result__snippet">Welcome to Python &amp; friends.</a>
  </div>
</div>
"""


async def _call(name: str, **kw: Any) -> Any:
    async with Client(mcp) as client:
        return (await client.call_tool(name, kw)).data


async def test_server_exposes_search() -> None:
    async with Client(mcp) as client:
        assert "search" in {t.name for t in await client.list_tools()}


def test_parse_ddg_html_unwraps_redirects_and_cleans() -> None:
    results = app.parse_ddg_html(_DDG_FIXTURE, limit=5)
    assert len(results) == 2
    assert results[0].title == "Example & A"  # entities unescaped, <b> stripped
    assert results[0].url == "https://example.com/a"  # uddg redirect unwrapped
    assert results[0].snippet == "The first result snippet."
    assert results[1].url == "https://python.org/"  # direct URL passes through
    assert results[1].snippet == "Welcome to Python & friends."


def test_parse_ddg_html_respects_limit() -> None:
    assert len(app.parse_ddg_html(_DDG_FIXTURE, limit=1)) == 1


def test_real_url_variants() -> None:
    assert app._real_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.com%2Fx") == "https://a.com/x"
    assert app._real_url("https://direct.example/") == "https://direct.example/"
    assert app._real_url("//cdn.example/y").startswith("https://cdn.example/y")


def test_resolve_backend_prefers_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.settings, "backend", "auto")
    monkeypatch.setattr(app.settings, "brave_key", "")
    monkeypatch.setattr(app.settings, "exa_key", "")
    assert app.resolve_backend() == "duckduckgo"
    monkeypatch.setattr(app.settings, "exa_key", "k")
    assert app.resolve_backend() == "exa"
    monkeypatch.setattr(app.settings, "brave_key", "k")
    assert app.resolve_backend() == "brave"  # brave wins over exa
    monkeypatch.setattr(app.settings, "backend", "duckduckgo")
    assert app.resolve_backend() == "duckduckgo"  # explicit setting overrides


async def test_search_tool_formats_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(query: str, limit: int) -> list[app.SearchResult]:
        return [app.SearchResult(title="First", url="https://one.example", snippet="snip one")]

    monkeypatch.setattr(app.settings, "backend", "duckduckgo")
    monkeypatch.setitem(app._BACKENDS, "duckduckgo", fake)
    out = await _call("search", query="hello", max_results=3)
    assert "1. First" in out and "https://one.example" in out and "snip one" in out


async def test_keyed_backend_without_key_gives_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.settings, "backend", "brave")
    monkeypatch.setattr(app.settings, "brave_key", "")
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="HEIM_SEARCH_BRAVE_KEY"):
        await _call("search", query="hello")
