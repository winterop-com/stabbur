"""A FastMCP server exposing a locked-down Python code sandbox over stdio.

Gives an assistant a scratchpad / calculator: run a Python snippet in a throwaway Docker
container (no network, read-only filesystem, capped memory/CPU/pids, wall-clock timeout) and get
its output back. The sandbox is :mod:`heim_sandbox` (shared with the benchmark). Requires a
running Docker daemon; without one the tool returns a clear error instead of executing.

Run standalone over stdio: ``heim-mcp-exec`` (or ``python -m heim.mcp_servers.exec``). Point heim at it
with ``heim chat --mcp exec`` or a ``[[mcp]]`` block.
"""

import asyncio
from typing import Any

from fastmcp import FastMCP
from heim_sandbox import DockerError, run_code

mcp: FastMCP = FastMCP("heim-exec")


@mcp.tool
def run_python(code: str, stdin: str = "", timeout_s: float = 10.0) -> dict[str, Any]:
    """Run a Python 3.13 snippet in a sandbox and return its output. Use for calculation/data work.

    Write a full program that PRINTS what you want back (only stdout/stderr are returned) — e.g.
    `print(sum(range(101)))`. `stdin` is fed to the program's standard input. The sandbox has NO
    network and NO persistent filesystem (nothing carries over between calls), a read-only root
    with a writable /tmp, and is killed at `timeout_s` seconds. `ok` is true only on a clean exit.
    """
    timeout_s = max(1.0, min(float(timeout_s), 60.0))  # bound the model-supplied timeout server-side
    try:
        result = run_code("python", code, stdin=stdin, timeout_s=timeout_s)
    except DockerError as exc:
        return {"ok": False, "error": f"{exc} — the exec sandbox needs a running Docker daemon."}
    return {"ok": result.exit_code == 0 and not result.timed_out, **result.model_dump()}


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn); quiet on Ctrl-C."""
    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
