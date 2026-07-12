"""A FastMCP server exposing read-only git inspection, sandboxed to one repository, over stdio.

Lets a local assistant answer "what changed / who wrote this / show me that commit" questions about
a single repository without any ability to mutate it or reach the network:

* **sandboxed** — every command runs as ``git -C <repo_root> …`` against exactly one directory
  (``HEIM_GIT_REPO_ROOT``, default the current directory). A ``repo_root`` that isn't a git work
  tree is refused with a clear error. Path arguments are contained to the repo (no absolute paths,
  no ``..`` escapes).
* **read-only** — each tool builds a *fixed* argv (no arbitrary subcommand passthrough), so there's
  no ``fetch`` / ``clone`` / ``push`` / ``commit`` surface. Writes are gated behind
  ``HEIM_GIT_ALLOW_WRITE`` (off by default); no mutating tool is registered while it's off, and none
  ship today — the server is inspection-only by design.
* **bounded** — commands run without a shell (a fixed argv; no pipes, globs, or ``$VAR`` expansion),
  with a per-command timeout and a capped output so a huge repo can't hang the loop or blow memory.

Run standalone over stdio: ``heim-mcp-git`` (or ``python -m heim.mcp_servers.git``). Point heim at it with
``heim chat --mcp git`` (opt-in; add it deliberately with ``heim mcp add git``).
"""

import os
import shutil
import subprocess
from pathlib import Path

from fastmcp import FastMCP
from pydantic_settings import BaseSettings, SettingsConfigDict

_TIMEOUT = 15.0  # seconds; a wedged git command can't stall a chat
_MAX_OUTPUT = 64 * 1024  # cap returned text so a huge diff/log can't blow memory
_MAX_LOG = 100  # hard ceiling on `git_log` count regardless of the request


class GitSettings(BaseSettings):
    """Config via ``HEIM_GIT_*`` env: which repo to inspect and whether writes are allowed."""

    model_config = SettingsConfigDict(env_prefix="HEIM_GIT_", extra="ignore")

    repo_root: Path = Path(".")  # HEIM_GIT_REPO_ROOT — the only directory git commands run in
    allow_write: bool = False  # HEIM_GIT_ALLOW_WRITE — reserved gate; no mutating tools ship yet


def _git_env() -> dict[str, str]:
    """Environment that neutralizes prompts/pager/color so git stays non-interactive and parseable."""
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",  # never block on a credential prompt (defense: no network ops)
        "GIT_OPTIONAL_LOCKS": "0",  # don't take locks for read commands
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    }


def _cap(text: str) -> str:
    """Truncate ``text`` to the output cap, flagging when it was cut."""
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + f"\n... [truncated at {_MAX_OUTPUT} bytes]"


def _run(root: Path, args: list[str]) -> tuple[int, str, str]:
    """Run ``git -c color.ui=false -C <root> <args…>`` without a shell; return (code, stdout, stderr).

    A fixed argv (never ``shell=True``), a hard timeout, and a hardened env. Raises ``RuntimeError``
    only for infrastructure failures (git missing, spawn error, timeout) — a non-zero git exit is
    returned to the caller so it can craft a tailored message.
    """
    exe = shutil.which("git")
    if exe is None:
        raise RuntimeError("git is not installed — install it and try again.")
    argv = [exe, "-c", "color.ui=false", "-C", str(root), *args]
    try:
        proc = subprocess.run(  # noqa: S603
            argv, capture_output=True, text=True, timeout=_TIMEOUT, env=_git_env(), check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git {args[0] if args else ''} timed out after {_TIMEOUT:.0f}s") from exc
    except OSError as exc:
        raise RuntimeError(f"couldn't run git: {exc}") from exc
    return proc.returncode, proc.stdout, proc.stderr


def _run_ok(root: Path, args: list[str]) -> str:
    """Run a git command and return its stdout, raising a clear ``RuntimeError`` on a non-zero exit."""
    code, out, err = _run(root, args)
    if code != 0:
        detail = err.strip() or out.strip() or f"exit {code}"
        raise RuntimeError(f"git {args[0]} failed: {detail[:300]}")
    return _cap(out)


def _require_repo(settings: GitSettings) -> Path:
    """Resolve ``repo_root`` and confirm it's a git work tree, or raise a clear error."""
    root = settings.repo_root.expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"repo_root {settings.repo_root!s} is not a directory")
    code, out, _ = _run(root, ["rev-parse", "--is-inside-work-tree"])
    if code != 0 or out.strip() != "true":
        raise RuntimeError(f"repo_root {root!s} is not a git work tree (no repository found there)")
    return root


def _safe_rel(root: Path, rel: str) -> str:
    """Contain ``rel`` to ``root``, returning a repo-relative posix path; reject absolute / ``..`` escapes."""
    if not rel:
        raise ValueError("empty path")
    candidate = Path(rel)
    if candidate.is_absolute():
        raise ValueError(f"invalid path {rel!r}: must be relative to the repo root")
    resolved = (root / candidate).resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise ValueError(f"invalid path {rel!r}: escapes the repo root")
    return resolved.relative_to(root).as_posix() or "."


def _check_ref(ref: str) -> str:
    """Validate a ref/commit argument: non-empty and not an option (no leading ``-``, no newline)."""
    ref = ref.strip()
    if not ref:
        raise ValueError("empty ref")
    if ref.startswith("-") or "\n" in ref:
        raise ValueError(f"invalid ref {ref!r}")
    return ref


def build_server(settings: GitSettings) -> FastMCP:
    """Build the FastMCP server, registering read tools; write tools stay gated behind ``allow_write``.

    Read tools are always registered. There are no mutating git tools today (the server is
    inspection-only), so nothing is added when ``allow_write`` is on — the gate exists so that any
    future write tool is registered here *only* when writes are enabled, and is absent entirely
    otherwise.
    """
    server: FastMCP = FastMCP("heim-git")

    @server.tool
    def git_status() -> str:
        """Working-tree status of the sandboxed repo: current branch plus staged/unstaged/untracked changes.

        Porcelain short form (``git status --short --branch``): each line is a two-column XY code and a
        path. An empty change list means a clean tree.
        """
        root = _require_repo(settings)
        return _run_ok(root, ["status", "--short", "--branch"]) or "clean"

    @server.tool
    def git_log(count: int = 20, path: str = "") -> str:
        """Recent commits, one per line: ``<short-hash> <date> <author> <subject>``.

        ``count`` is clamped to 1..100. Pass ``path`` (relative to the repo root) to limit history to
        a single file or directory.
        """
        root = _require_repo(settings)
        n = max(1, min(int(count), _MAX_LOG))
        args = ["log", f"-n{n}", "--no-color", "--date=short", "--pretty=format:%h %ad %an %s"]
        if path:
            args += ["--", _safe_rel(root, path)]
        return _run_ok(root, args) or "no commits"

    @server.tool
    def git_diff(ref: str = "", path: str = "") -> str:
        """Unified diff of the working tree (default) or of a ref/range (e.g. ``HEAD~1``, ``main..HEAD``).

        Optional ``path`` (relative to the repo root) restricts the diff to that file or directory.
        Output is capped; a very large diff is truncated with a marker.
        """
        root = _require_repo(settings)
        args = ["diff", "--no-color"]
        if ref:
            args.append(_check_ref(ref))
        if path:
            args += ["--", _safe_rel(root, path)]
        return _run_ok(root, args) or "no differences"

    @server.tool
    def git_show(ref: str) -> str:
        """Show a commit (or any object) by ref: metadata plus the diff it introduced (``git show <ref>``).

        ``ref`` is any revision git understands — a short/long hash, ``HEAD``, a tag, ``HEAD~2``.
        Output is capped for a large commit.
        """
        root = _require_repo(settings)
        return _run_ok(root, ["show", "--no-color", "--stat", "--patch", _check_ref(ref)])

    @server.tool
    def git_branches() -> list[str]:
        """List all branches (local and remote-tracking) in the sandboxed repo, as short names."""
        root = _require_repo(settings)
        out = _run_ok(root, ["branch", "--all", "--no-color", "--format=%(refname:short)"])
        return [line.strip() for line in out.splitlines() if line.strip()]

    @server.tool
    def git_ls_files(pattern: str = "") -> list[str]:
        """List tracked files in the repo, optionally filtered by a glob pathspec (e.g. ``*.py``, ``src/*``).

        Paths are relative to the repo root. The optional ``pattern`` is applied as a git pathspec; it
        may not be absolute, escape the repo (``..``), or look like an option.
        """
        root = _require_repo(settings)
        args = ["ls-files"]
        if pattern:
            if pattern.startswith("-"):
                raise ValueError(f"invalid pattern {pattern!r}")
            if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                raise ValueError(f"invalid pattern {pattern!r}: must stay within the repo")
            args += ["--", pattern]
        out = _run_ok(root, args)
        files = [line for line in out.splitlines() if line]
        return files[:5000]

    @server.tool
    def git_blame(path: str, start: int | None = None, end: int | None = None) -> str:
        """Line-by-line authorship for a tracked file (``git blame``), optionally for a line range.

        ``path`` is relative to the repo root and must stay inside it. Pass ``start`` (and optionally
        ``end``) to blame only lines ``start..end`` — cheaper and clearer than the whole file.
        """
        root = _require_repo(settings)
        rel = _safe_rel(root, path)
        args = ["blame", "--date=short"]
        if start is not None:
            if start < 1:
                raise ValueError("start must be >= 1")
            span = f"{start}," + (str(end) if end is not None else "")
            if end is not None and end < start:
                raise ValueError("end must be >= start")
            args += ["-L", span]
        args += ["--", rel]
        return _run_ok(root, args)

    if settings.allow_write:
        # No mutating git tools ship yet — this server is inspection-only by design (never network
        # ops). Any future write tool must be registered here so it stays gated behind allow_write
        # and is absent entirely when writes are off.
        pass

    return server


mcp: FastMCP = build_server(GitSettings())


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn). Swallow shutdown noise."""
    import asyncio  # noqa: PLC0415

    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
