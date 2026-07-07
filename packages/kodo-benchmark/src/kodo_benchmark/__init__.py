"""Kodo benchmarks: language coding + tool-use suites with a Docker-sandboxed executor.

Re-exports the FastMCP app (``mcp``) and stdio entry point (``main``), plus the ``core``
API (suites, ``evaluate``, ``run_code``, ``extract_code``, and the result models) that
kodo's ``kodo benchmark`` driver imports directly.
"""

from typing import Any

from kodo_benchmark.core import (
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


def __getattr__(name: str) -> Any:  # noqa: D401
    """Lazily import ``main`` / ``mcp`` from ``.app`` (PEP 562).

    The ``kodo benchmark`` plugin imports only ``core`` (cheap). Keeping ``main``/``mcp`` — which
    pull in FastMCP — out of eager package import means plugin discovery (and thus every ``kodo``
    CLI startup, which mounts plugins) doesn't pay the ~0.2s FastMCP import it never uses.
    """
    if name in ("main", "mcp"):
        import importlib  # noqa: PLC0415

        return getattr(importlib.import_module("kodo_benchmark.app"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
