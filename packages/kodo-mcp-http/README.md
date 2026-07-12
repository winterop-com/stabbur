# kodo-mcp-http

An [MCP](https://modelcontextprotocol.io) server exposing a **first-party, SSRF-guarded,
allowlisted HTTP fetch** tool. It gives a local assistant a narrow "call this API / read this
endpoint" primitive without the open-ended fetch surface a general HTTP client would hand a model.
Kodo is the MCP *client*; this is one of the servers it can spawn.

Two tools:

- **`http_get(url, headers?)`** → the response's `status`, final `url`, `content_type`, and body
  `text` (capped, `truncated` when cut).
- **`http_head(url, headers?)`** → the same metadata (`status`, `content_type`, `content_length`)
  with no body — check existence/type/size before a GET.

Both return a structured result (`ok` + fields, or `ok=false` + `error`) — blocks, timeouts, and
oversize aborts come back as data, never an exception.

## Safe by default (empty allowlist = deny all)

The allowlist is **empty by default**, so every request is refused with a clear
`no hosts allowlisted` message until you opt hosts in. This is fail-closed on purpose: a fresh
install can't reach anything. Opt hosts in with `KODO_MCP_HTTP_ALLOWLIST` (comma-separated). A
host matches an entry exactly or as a subdomain — `api.example.com` matches an entry of
`example.com`; `notexample.com` does not.

```bash
export KODO_MCP_HTTP_ALLOWLIST="example.com,api.acme.io"
```

## Security (SSRF model)

For the top-level URL **and every redirect hop**:

1. **Scheme** — only `http` / `https`.
2. **Allowlist** — the host must match `KODO_MCP_HTTP_ALLOWLIST` (empty ⇒ deny all).
3. **Resolve + vet** — the host is resolved once and *every* resolved IP checked; any private /
   loopback / link-local / reserved / CGNAT address (`not is_global`, plus multicast) is refused.
4. **Pin** — the connection is made to the vetted IP, with the `Host` header and TLS SNI/cert
   verification kept on the original hostname. Connecting to the checked IP (instead of letting the
   client re-resolve) closes the DNS-rebinding window between the check and the fetch.

Redirects are followed **manually** (auto-follow disabled) so each hop re-runs all four checks — a
public page can't redirect the fetch to an internal address. Reach an internal/localhost host on
purpose with `KODO_MCP_HTTP_ALLOW_PRIVATE=1` (still allowlist-gated).

**Residual caveat:** the pinned-IP path closes the DNS-rebinding TOCTOU for the connection we open.
A fully airtight guarantee against rebinding on a *streaming* connection is hard (the same caveat
`kodo-mcp-web` documents for its browser path); resolve-vet-pin-once is the strong, practical
guarantee here.

## Config (`KODO_MCP_HTTP_*`)

| Var | Default | Meaning |
| --- | --- | --- |
| `KODO_MCP_HTTP_ALLOWLIST` | *(empty)* | Comma-separated allowed hosts/domains. Empty = deny all. |
| `KODO_MCP_HTTP_MAX_BYTES` | `1000000` | Cap on the response body (streamed + aborted past this). |
| `KODO_MCP_HTTP_TIMEOUT_S` | `15` | Per-request timeout (seconds). |
| `KODO_MCP_HTTP_ALLOW_PRIVATE` | `false` | Allow private/loopback hosts (skips the IP vet + pin). |
| `KODO_MCP_HTTP_MAX_REDIRECTS` | `5` | Redirect hops to follow; each is re-vetted. |
| `KODO_MCP_HTTP_USER_AGENT` | `kodo-mcp-http/…` | User-Agent sent to hosts. |

## Run it

```bash
kodo-mcp-http                    # run standalone over stdio
python -m kodo_mcp_http          # same, via the module
kodo mcp add http                # wire it into a project's ./.mcp.json
kodo chat --mcp kodo-mcp-http    # kodo spawns it and exposes http_get / http_head
```

## Package shape

Same workspace-member layout as the other bundled servers (see `kodo-mcp-network`):
`src/kodo_mcp_http/{__init__,__main__,app,plugin}.py` + `tests/`.
