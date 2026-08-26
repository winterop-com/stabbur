"""MCP server discovery + on/off toggles — the Tools panel's backing API.

``/api/tools`` answers "what can the agent call right now", which is empty on a fresh machine and
says nothing about the twelve ``heim-mcp-*`` servers heim ships. These routes answer the other half:
**what heim could run, and what is switched on** — so a client can render the whole shipped set with
checkboxes instead of the dead end that an empty tool list is.

The toggle writes the machine-global ``~/.config/heim/mcp.json`` (see :func:`heim.mcp_catalog.set_enabled`),
the same file ``heim mcp add --global`` writes, and is an **allow-list over the bundled set** — a name heim
doesn't ship is a 404, so no request can ever make heim spawn an arbitrary command. Restart semantics are
reported per call rather than assumed: an *enable* attaches live through the bridge's still-open exit stack;
a *disable* persists immediately but an already-spawned server keeps its subprocess (and its tools) until
``heim serve`` restarts, which the response says outright rather than pretending the tools are gone.
"""

from pathlib import Path

from fastapi import HTTPException, Request
from pydantic import BaseModel

from heim import mcp_catalog, mcpservers
from heim.models import BundledMcp
from heim.routers.serving._base import router
from heim.tools import MCPBridge


class McpToggleRequest(BaseModel):
    """The desired on/off state for one bundled server."""

    enabled: bool


class McpToggleResult(BaseModel):
    """The outcome of a toggle: the new persisted state, plus whether it is live *yet*."""

    server: BundledMcp
    applied: bool  # the change is in effect in this running server right now
    restart_required: bool  # `heim serve` must restart before the change takes effect
    detail: str = ""  # human-readable why, when applied is False or a caveat applies


def _bundled_or_500(project_dir: Path | None = None) -> list[BundledMcp]:
    """The bundled set, turning an unparseable ``mcp.json`` into a readable error, not a bare 500."""
    try:
        return mcp_catalog.bundled(project_dir)
    except mcpservers.McpConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/mcp/servers")
def list_mcp_servers() -> list[BundledMcp]:
    """Every first-party MCP server heim ships, with whether it is currently enabled.

    Sync (``def``) so the config reads run in a worker thread, off the loop. ``enabled`` is the resolved
    truth (global + project, honoring a project override or disable marker) — i.e. what heim spawns —
    and ``scope`` names the file that switches it on, so a client can explain why a server it can't
    disable globally is on. A server whose optional extra isn't installed is listed with
    ``installed=false`` and its install hint rather than hidden.
    """
    return _bundled_or_500()


@router.post("/api/mcp/servers/{name}")
async def set_mcp_server(name: str, body: McpToggleRequest, request: Request) -> McpToggleResult:
    """Switch one bundled MCP server on or off, persisting to the machine-global ``mcp.json``.

    Honest about what actually happened:

    - **enable** — persisted, then attached live via :meth:`heim.tools.MCPBridge.add_server`, so its tools
      are callable on the very next chat turn (``applied=true``). If the spawn fails (e.g. an optional
      server whose extra isn't installed) the failure and its reason ride back instead of a fake success.
    - **disable** — persisted immediately, but a server already spawned at startup keeps its subprocess
      and its tools until ``heim serve`` restarts, so that answers ``restart_required=true``. Disabling one
      that was never spawned applies at once.

    404 for a name heim doesn't bundle; 409 when a disable would require editing the project's own
    ``.mcp.json`` (which is committed and portable — heim never rewrites it from a web request).
    """
    try:
        entry = mcp_catalog.set_enabled(name, body.enabled)
    except mcp_catalog.UnknownServer as exc:
        raise HTTPException(status_code=404, detail=f"{name!r} is not a bundled MCP server") from exc
    except mcp_catalog.ProjectScoped as exc:
        raise HTTPException(
            status_code=409,
            detail=f"{name!r} is switched on by this project's {exc} — edit it (or `heim mcp remove {name}`).",
        ) from exc
    except mcpservers.McpConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    bridge: MCPBridge | None = getattr(request.app.state, "mcp_bridge", None)
    spec = mcp_catalog.to_server(entry)
    if body.enabled:
        if not entry.enabled:
            # Written to the global file, yet still not resolved: the project's .mcp.json carries a
            # disable marker for this name, which wins. A restart wouldn't help — say so.
            return McpToggleResult(
                server=entry,
                applied=False,
                restart_required=False,
                detail=f"this project's {mcpservers.project_path()} disables {name!r}; remove that entry to use it",
            )
        if bridge is None:  # no bridge (state-poking tests / a non-serve host) — can't attach in-process
            return McpToggleResult(
                server=entry, applied=False, restart_required=True, detail="restart heim serve to attach its tools"
            )
        attached, reason = await bridge.add_server(spec)
        return McpToggleResult(
            server=entry,
            applied=attached,
            # A spawn failure is not fixed by restarting — the command is missing or broken. Say why
            # instead, and let the install hint (surfaced by /api/mcp/servers) do the explaining.
            restart_required=False,
            detail="" if attached else f"could not start {name}: {reason}",
        )

    live = bridge is not None and bridge.is_live(spec)
    return McpToggleResult(
        server=entry,
        applied=not live,
        restart_required=live,
        detail="already running - its tools stay attached until heim serve restarts" if live else "",
    )
