"""Kodo MCP server: core utility tools (text, encoding, hashing, JSON, math, stats).

Re-exports the FastMCP app (``mcp``) and stdio entry point (``main``) so both the
``kodo-mcp-utils`` console script and ``python -m kodo_mcp_utils`` resolve here.
"""

from typing import Any

__all__ = ["main", "mcp"]


def __getattr__(name: str) -> Any:  # noqa: D401
    """Lazily import ``main`` / ``mcp`` from ``.app`` (PEP 562).

    Keeps package import cheap: plugin discovery imports this package (for ``plugin.py``) but
    must NOT drag in FastMCP — that ~0.1s+ import only matters when the server actually runs
    (the console script or ``python -m``). Deferring it here keeps ``kodo`` CLI startup fast.
    """
    if name in __all__:
        import importlib  # noqa: PLC0415

        return getattr(importlib.import_module(f"{__name__}.app"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
