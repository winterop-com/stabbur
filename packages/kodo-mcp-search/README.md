# kodo-mcp-search

An [MCP](https://modelcontextprotocol.io) server that **searches the web** and returns
a short list of titled results (title, URL, snippet), served over stdio. Pairs with
[`kodo-mcp-web`](../kodo-mcp-web): the model **searches**, then **reads** the winning URL.
Kodo is the MCP *client*; this is one of the servers it ships with.

One tool:

- **`search(query, max_results=0)`** → a numbered list of results (0 = server default count).

## Backends

`KODO_SEARCH_BACKEND` (default `auto`):

| Backend | Key | Notes |
| --- | --- | --- |
| `duckduckgo` | none | Scrapes the DuckDuckGo HTML endpoint. Zero-config default. |
| `brave` | `KODO_SEARCH_BRAVE_KEY` | Brave Search API (JSON). |
| `exa` | `KODO_SEARCH_EXA_KEY` | Exa API (JSON). |

`auto` uses Brave or Exa when its key is set, otherwise DuckDuckGo. Selecting a keyed
backend without its key returns a clear hint, not a crash.

It's **bundled** with kodo (pure `httpx` + stdlib parsing — light), so it works out of the
box. Point kodo at it:

```
kodo mcp add search                 # add it to ./kodo.toml
kodo-mcp-search                     # or run it standalone over stdio
kodo chat --mcp kodo-mcp-search     # kodo spawns it and exposes search
```

## Config (`KODO_SEARCH_*`)

| Var | Default | Meaning |
| --- | --- | --- |
| `KODO_SEARCH_BACKEND` | `auto` | `auto` / `duckduckgo` / `brave` / `exa`. |
| `KODO_SEARCH_BRAVE_KEY` | — | Brave Search API key. |
| `KODO_SEARCH_EXA_KEY` | — | Exa API key. |
| `KODO_SEARCH_MAX_RESULTS` | `5` | Default result count. |
| `KODO_SEARCH_TIMEOUT_SECONDS` | `15` | Per-request timeout. |

Follows the same workspace-member layout as `kodo-mcp-datetime` (see that package's README
for the template).
