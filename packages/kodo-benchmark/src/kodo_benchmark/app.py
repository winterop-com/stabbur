"""FastMCP server exposing the benchmark suites + Docker executor as tools.

kodo's ``kodo benchmark`` command drives suites deterministically by importing ``core``
directly; this MCP surface lets any MCP host (or a model self-driving in chat) reach the
same suites and sandboxed executor. Tools mirror ``core``: list suites, fetch a problem's
public part, evaluate a candidate solution, or run arbitrary code in the sandbox.

Run standalone over stdio: ``kodo-benchmark`` (or ``python -m kodo_benchmark``).
"""

import asyncio
from typing import Any

from fastmcp import FastMCP

from kodo_benchmark import core

mcp: FastMCP = FastMCP("kodo-benchmark")


@mcp.tool
def list_suites() -> list[str]:
    """Names of the available benchmark suites."""
    return core.available_suites()


@mcp.tool
def get_problem(suite: str, problem_id: str) -> dict[str, Any]:
    """A problem's public part (prompt, language, timeout) — never its hidden test cases."""
    s = core.load_suite(suite)
    problem = next((p for p in s.problems if p.id == problem_id), None)
    if problem is None:
        raise ValueError(f"suite {suite!r} has no problem {problem_id!r}")
    return {"id": problem.id, "prompt": problem.prompt, "language": problem.language, "timeout_s": problem.timeout_s}


@mcp.tool
def evaluate(suite: str, problem_id: str, code: str) -> dict[str, Any]:
    """Run ``code`` against a problem's hidden tests in the sandbox; return the scored result."""
    s = core.load_suite(suite)
    problem = next((p for p in s.problems if p.id == problem_id), None)
    if problem is None:
        raise ValueError(f"suite {suite!r} has no problem {problem_id!r}")
    return core.evaluate(problem, code).model_dump()


@mcp.tool
def run_code(language: str, code: str, stdin: str = "", timeout_s: float = 10.0) -> dict[str, Any]:
    """Execute arbitrary ``code`` in the locked-down container and return its raw output."""
    return core.run_code(language, code, stdin=stdin, timeout_s=timeout_s).model_dump()


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn); quiet on Ctrl-C."""
    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
