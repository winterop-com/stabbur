"""Kodo MCP server: language coding benchmarks with a Docker-sandboxed executor.

Re-exports the FastMCP app (``mcp``) and stdio entry point (``main``), plus the ``core``
API (suites, ``evaluate``, ``run_code``, ``extract_code``, and the result models) that
kodo's ``kodo benchmark`` driver imports directly.
"""

from kodo_mcp_benchmark.app import main, mcp
from kodo_mcp_benchmark.core import (
    Problem,
    ProblemResult,
    RunRecord,
    RunResult,
    Suite,
    SuiteReport,
    available_suites,
    docker_available,
    evaluate,
    extract_code,
    load_results,
    load_suite,
    make_record,
    render_leaderboard,
    result_path,
    run_code,
    save_run,
    score_tool,
)

__all__ = [
    "Problem",
    "ProblemResult",
    "RunRecord",
    "RunResult",
    "Suite",
    "SuiteReport",
    "available_suites",
    "docker_available",
    "evaluate",
    "extract_code",
    "load_results",
    "load_suite",
    "main",
    "make_record",
    "mcp",
    "render_leaderboard",
    "result_path",
    "run_code",
    "save_run",
    "score_tool",
]
