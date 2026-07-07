"""A FastMCP server that runs shell commands on the host — the "escape the box" tool.

The long tail of "just run X" (ping, df, ps, dig, journalctl, git log, …) can't be a tool each,
and the sandboxed ``exec`` server is network-off/fs-isolated by design. This server runs commands
on the **real host**, with a safety ladder so that's not a footgun:

* **Read-only by default** — only a curated allowlist of non-mutating, no-data-egress commands is
  permitted (network *diagnostics* like ``ping``/``dig``, host/process/disk inspection like
  ``df``/``ps``/``journalctl``, read-only file commands, and the read subcommands of ``git`` /
  ``systemctl`` / ``docker`` / ``kubectl``). Anything else is refused. Commands run **without a
  shell** (a fixed argv), so there are no pipes, globs, ``$VAR`` expansion, or injection.
* **Full mode, opt-in** — set ``KODO_SHELL_UNRESTRICTED=1`` to run *arbitrary* commands through a
  shell (pipes/redirects/globs work). That's real host access; enable it only when you trust the
  loop. Deliberately excluded from read-only: ``curl``/``wget``/``nc`` (data egress) and ``env``
  (secret leak) — use full mode for those.

Every run is bounded by a timeout and its output is capped. Not seeded by ``kodo setup``; add it
deliberately with ``kodo mcp add shell``.
"""

import os
import shlex
import shutil
import subprocess
from typing import Any

from fastmcp import FastMCP

mcp: FastMCP = FastMCP("kodo-shell")

_TIMEOUT = 30.0  # seconds; a wedged command can't stall a chat
_MAX_OUTPUT = 64 * 1024  # per stream (stdout/stderr); truncation is flagged

# Non-mutating, no-data-egress binaries allowed in read-only mode. Excludes anything that can send
# data out (curl/wget/nc — exfiltration under prompt injection) or dump the environment (env).
_READ_ONLY: frozenset[str] = frozenset(
    {
        # network diagnostics (no data payload)
        "ping", "ping6", "dig", "host", "nslookup", "traceroute", "traceroute6", "tracepath",
        "mtr", "ss", "netstat", "ip", "ifconfig", "route", "arp", "whois", "getent",
        # host / process / disk / logs
        "uname", "uptime", "hostname", "hostnamectl", "whoami", "id", "date", "cal", "df", "du",
        "free", "lsblk", "lscpu", "lsusb", "lspci", "lsof", "ps", "pgrep", "pstree", "w", "who",
        "last", "vmstat", "nproc", "journalctl", "dmesg", "sensors", "loadavg",
        # files + output (read-only)
        "ls", "cat", "head", "tail", "wc", "stat", "file", "tree", "readlink", "realpath",
        "basename", "dirname", "pwd", "which", "type", "md5sum", "sha256sum", "echo", "printf",
    }
)  # fmt: skip

# Dual-use binaries: allowed in read-only mode only for their read subcommands (argv[1]).
_READ_ONLY_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset(
        {"status", "log", "diff", "show", "branch", "remote", "describe", "rev-parse", "tag", "blame", "shortlog"}
    ),
    "systemctl": frozenset(
        {"status", "is-active", "is-enabled", "is-failed", "list-units", "list-unit-files", "show", "cat"}
    ),
    "docker": frozenset({"ps", "images", "version", "info", "logs", "inspect", "stats", "top", "port", "system"}),
    "kubectl": frozenset({"get", "describe", "logs", "top", "version", "cluster-info", "api-resources", "explain"}),
    "pip": frozenset({"list", "show", "freeze"}),
    "npm": frozenset({"list", "ls", "view", "outdated"}),
}


def _unrestricted() -> bool:
    """Whether full (arbitrary-command) mode is enabled via ``KODO_SHELL_UNRESTRICTED``."""
    return os.environ.get("KODO_SHELL_UNRESTRICTED", "").strip().lower() in ("1", "true", "yes", "on")


def _check_allowed(argv: list[str]) -> None:
    """Raise if ``argv`` isn't permitted in read-only mode (a no-op in full mode)."""
    binary = os.path.basename(argv[0])
    if binary in _READ_ONLY:
        return
    subs = _READ_ONLY_SUBCOMMANDS.get(binary)
    if subs is not None:
        sub = argv[1] if len(argv) > 1 else ""
        if sub in subs:
            return
        raise RuntimeError(
            f"read-only mode: `{binary} {sub}` is not an allowed read subcommand of {binary!r}. "
            "Set KODO_SHELL_UNRESTRICTED=1 to run arbitrary commands."
        )
    raise RuntimeError(
        f"read-only mode: {binary!r} is not in the allowlist. Allowed: network diagnostics (ping, dig, …), "
        "host/process/disk inspection (df, ps, journalctl, …), read-only file commands, and read "
        "subcommands of git/systemctl/docker/kubectl. Set KODO_SHELL_UNRESTRICTED=1 for arbitrary commands."
    )


@mcp.tool
def run(command: str) -> dict[str, Any]:
    """Run a shell command on this machine and return its ``stdout``, ``stderr``, and ``exit_code``.

    **Read-only by default**: only non-mutating, no-egress commands are allowed (``ping``, ``dig``,
    ``df``, ``ps``, ``journalctl``, ``ls``/``cat``, ``git log``, ``systemctl status``, ``docker ps``,
    …), and the command runs without a shell — so no pipes, globs, or ``$VAR`` expansion. If
    ``KODO_SHELL_UNRESTRICTED=1`` is set, it instead runs the command through a shell with **no
    restrictions** (real host access). Bounded by a timeout; output is capped (``truncated`` flags it).
    """
    if not command.strip():
        raise RuntimeError("empty command")
    try:
        if _unrestricted():
            argv_full = ["bash", "-lc", command]
            proc = subprocess.run(argv_full, capture_output=True, text=True, timeout=_TIMEOUT)  # noqa: S603
        else:
            try:
                argv = shlex.split(command)
            except ValueError as exc:
                raise RuntimeError(f"couldn't parse the command: {exc}") from exc
            if not argv:
                raise RuntimeError("empty command")
            _check_allowed(argv)
            exe = shutil.which(argv[0])
            if exe is None:
                raise RuntimeError(f"command not found on PATH: {argv[0]!r}")
            proc = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, timeout=_TIMEOUT)  # noqa: S603
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"command timed out after {_TIMEOUT:.0f}s") from exc
    except OSError as exc:
        raise RuntimeError(f"couldn't run the command: {exc}") from exc
    stdout, stderr = proc.stdout or "", proc.stderr or ""
    return {
        "exit_code": proc.returncode,
        "stdout": stdout[:_MAX_OUTPUT],
        "stderr": stderr[:_MAX_OUTPUT],
        "truncated": len(stdout) > _MAX_OUTPUT or len(stderr) > _MAX_OUTPUT,
        "mode": "unrestricted" if _unrestricted() else "read-only",
    }


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn). Swallow shutdown noise."""
    import asyncio  # noqa: PLC0415

    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
