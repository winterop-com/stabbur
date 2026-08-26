"""Stabbur MCP server: read-only git inspection, sandboxed to one configured repository.

Re-exports the FastMCP app (``mcp``) and stdio entry point (``main``) from ``app`` so both the
``stabbur-mcp-git`` console script and ``python -m stabbur.mcp_servers.git`` resolve against the package root.
Import stays cheap (FastMCP is deferred) so plugin discovery doesn't pay for it.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .app import main as main
    from .app import mcp as mcp

__all__ = ["main", "mcp"]


def __getattr__(name: str) -> Any:  # noqa: D401
    """Lazily import ``main`` / ``mcp`` from ``.app`` (PEP 562) — keeps package import cheap."""
    if name in __all__:
        import importlib  # noqa: PLC0415

        return getattr(importlib.import_module(f"{__name__}.app"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
