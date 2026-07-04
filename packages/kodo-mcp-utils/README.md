# kodo-mcp-utils

Core utility tools for the assistant — all stdlib, deterministic (no network, no
randomness, no filesystem), so they're safe to expose and easy to benchmark.

- **Text**: reverse, upper/lower/title case, slugify, word/char count.
- **Encoding**: Base64, URL, and hex encode/decode.
- **Hashing**: SHA-256, MD5 (checksums).
- **JSON**: pretty-print, minify, top-level keys.
- **Math**: `calc` (safe arithmetic via a restricted AST — never `eval`), factorial,
  gcd/lcm, is_prime, base conversion.
- **Stats**: mean, median, sum.

Run standalone over stdio (any MCP host), or point kodo at it:

```
kodo-mcp-utils                    # or: python -m kodo_mcp_utils
kodo chat --mcp utils             # kodo resolves the advertised name to the command
```

Follows the `kodo-mcp-datetime` template (src layout, `__init__` + `__main__` + `app.py`);
`plugin.py` is an advertise-only pluginkit extension so kodo discovers it without
hardcoding. Benchmarked by the `tools-utils` suite in `kodo-benchmark`.
