# kodo-mcp-exec

An MCP server giving an assistant a **sandboxed Python scratchpad**: `run_python(code, stdin)`
executes a snippet in a throwaway Docker container (no network, read-only filesystem, capped
memory/CPU/pids, wall-clock timeout — via [`kodo-sandbox`](../kodo-sandbox)) and returns its
stdout/stderr/exit code. Print what you want back; nothing persists between calls.

Needs a running Docker daemon (the tool returns a clear error without one). Run standalone over
stdio: `kodo-mcp-exec` (or `python -m kodo_mcp_exec`). Attach with `kodo chat --mcp exec`, or
`kodo mcp add exec` in a project.
