# kodo-mcp-memory

A tiny MCP server giving an assistant **persistent notes / key-value memory** across sessions.

Tools: `memory_set`, `memory_get`, `memory_list`, `memory_search`, `memory_delete`.

Storage is a single JSON file that travels with the library:

- `KODO_MEMORY_DIR/notes.json` if set, else
- `<KODO_LIBRARY_ROOT>/.kodo/memory/notes.json` (next to the library's other metadata), else
- `./.kodo/memory/notes.json` (project-local fallback).

Run standalone over stdio: `kodo-mcp-memory` (or `python -m kodo_mcp_memory`). Attach it with
`kodo chat --mcp memory`, or `kodo mcp add memory` in a project.
