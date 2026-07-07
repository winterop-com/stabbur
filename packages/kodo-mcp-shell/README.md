# kodo-mcp-shell

MCP server to run host shell commands. Read-only allowlist (ping, df, ps, journalctl, git log, ...) by default; set `KODO_SHELL_UNRESTRICTED=1` for arbitrary commands. Opt-in: add with `kodo mcp add shell`.
