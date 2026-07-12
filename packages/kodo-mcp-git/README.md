# kodo-mcp-git

MCP server for **read-only git inspection**, sandboxed to a single repository. Lets an assistant
answer "what changed / who wrote this / show me that commit" without any ability to mutate the repo
or reach the network.

- **Read-only** — each tool builds a *fixed* argv (no arbitrary subcommand passthrough), so there's
  no `fetch` / `clone` / `push` / `commit` surface. Writes are gated behind `KODO_GIT_ALLOW_WRITE`
  (off by default); no mutating tool ships today.
- **Sandboxed** — every command runs as `git -C <repo_root> …` against exactly one directory. A
  `repo_root` that isn't a git work tree is refused. Path arguments are contained to the repo (no
  absolute paths, no `..` escapes).
- **Bounded** — commands run without a shell (a fixed argv; no pipes/globs/`$VAR`), with a per-command
  timeout and a 64 KB output cap so a huge repo can't hang the loop or blow memory.

## Tools

| Tool | What it does |
| --- | --- |
| `git_status` | Working-tree status (branch + staged/unstaged/untracked), porcelain short form. |
| `git_log(count=20, path="")` | Recent commits as `hash date author subject`; `count` clamped to 1..100. |
| `git_diff(ref="", path="")` | Unified diff of the working tree, or of a ref/range (e.g. `HEAD~1`, `main..HEAD`). |
| `git_show(ref)` | A commit/object by ref: metadata + the diff it introduced. |
| `git_branches()` | All branches (local + remote-tracking) as short names. |
| `git_ls_files(pattern="")` | Tracked files, optionally filtered by a glob pathspec (e.g. `*.py`). |
| `git_blame(path, start=None, end=None)` | Line-by-line authorship for a tracked file, optional line range. |

## Config

Environment (`KODO_GIT_*`):

- `KODO_GIT_REPO_ROOT` — the only directory git commands run in (default: the current directory).
- `KODO_GIT_ALLOW_WRITE` — reserved gate for future mutating tools; off by default (none ship yet).

## Run

Standalone over stdio: `kodo-mcp-git` (or `python -m kodo_mcp_git`). Point kodo at it with
`kodo chat --mcp git`, or add it to a project with `kodo mcp add git`.
