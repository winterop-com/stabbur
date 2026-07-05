# kodo-mcp-files

An MCP server giving an assistant **read-only file access** under one configured root:

- `list_files(subdir)` — list entries (skips `.git`, `node_modules`, `.venv`, caches, …)
- `read_file(path)` — read a text file (binary/oversized files refused)
- `search_files(query, subdir)` — grep for lines across text files
- `write_file(path, content)` — **off by default**; needs `KODO_FILES_WRITABLE=1`

Every path is contained to the root by `safe_join` (no absolute paths, no `..` escapes). Config
via `KODO_FILES_ROOT` (default: current directory) and `KODO_FILES_WRITABLE`.

Run standalone over stdio: `kodo-mcp-files` (or `python -m kodo_mcp_files`). Attach with
`kodo chat --mcp files`, or `kodo mcp add files` in a project.
