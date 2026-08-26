"""MCP server discovery + on/off toggles — the Tools panel's backing API.

``/api/tools`` answers "what can the agent call right now", which is empty on a fresh machine and
says nothing about the twelve ``heim-mcp-*`` servers heim ships. These routes answer the other half:
**what heim could run, and what is switched on** — so a client can render the whole shipped set with
checkboxes instead of the dead end that an empty tool list is.

They also answer the follow-up question a checkbox alone leaves hanging: **what is this server configured
to do**. A bundled server declares the env it reads, and the GET reports both what is persisted and the
value in force (an unset ``HEIM_FILES_ROOT`` resolved to the absolute directory ``heim serve`` runs in) —
so a card can say "rooted at /Users/me/dev/thing" instead of "a configured workspace root", and the POST
can change it without sending anyone to hand-edit JSON.

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


class McpUpdateRequest(BaseModel):
    """A change to one bundled server: its on/off state, its declared env settings, or both.

    Both fields are optional so a client sends only what it is changing — a settings edit must not
    have to restate ``enabled`` (and risk flipping it from a stale row). Applied env-first, so
    ``{"env": {...}, "enabled": true}`` spawns the server *with* the new settings in one call.
    """

    enabled: bool | None = None
    env: dict[str, str] | None = None  # declared variables only; "" clears one back to its default


class McpUpdateResult(BaseModel):
    """The outcome of a change: the new persisted state, plus whether it is live *yet*."""

    server: BundledMcp
    applied: bool  # the change is in effect in this running server right now
    restart_required: bool  # `heim serve` must restart before the change takes effect
    detail: str = ""  # human-readable why, when applied is False or a caveat applies


_STALE_ENV = "already running - restart heim serve to apply the new settings"


def _set_env_or_error(name: str, env: dict[str, str]) -> BundledMcp:
    """Persist declared settings for one server, turning each refusal into its own status code.

    Every branch here is a refusal to do something surprising, so each gets a distinct code and a
    message that names the fix: 404 unknown server, 400 undeclared variable (the allow-list that keeps
    a request from injecting arbitrary env into a spawned process), 409 for the two cases where the
    write would land somewhere it can't take effect — a project-owned server, or one that is switched
    off and would be switched *on* by gaining an entry.
    """
    try:
        return mcp_catalog.set_env(name, env)
    except mcp_catalog.UnknownServer as exc:
        raise HTTPException(status_code=404, detail=f"{name!r} is not a bundled MCP server") from exc
    except mcp_catalog.UnknownSetting as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mcp_catalog.NotConfigured as exc:
        raise HTTPException(status_code=409, detail=f"switch {name!r} on before configuring it") from exc
    except mcp_catalog.ProjectScoped as exc:
        raise HTTPException(
            status_code=409,
            detail=f"{name!r} comes from this project's {exc} — set its env there.",
        ) from exc
    except mcpservers.McpConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _env_applied(bridge: MCPBridge | None, entry: BundledMcp) -> bool:
    """Whether the just-persisted settings are in force in *this* process.

    No bridge means no MCP subprocess exists here at all (a non-serve host / a state-poking test), so
    the file is the whole truth; otherwise only a not-yet-spawned server can pick the change up.
    """
    return bridge is None or bridge.update_server(mcp_catalog.to_server(entry))


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

    ``env`` is what the resolving ``mcp.json`` entry persists (usually nothing) and ``settings`` is the
    server's declared knobs, each carrying the value **actually in force** — the configured one, else the
    resolved default. Both are filled in whether or not the server is on, because "which directory can it
    see" is a question asked before switching it on as often as after.
    """
    return _bundled_or_500()


@router.post("/api/mcp/servers/{name}")
async def set_mcp_server(name: str, body: McpUpdateRequest, request: Request) -> McpUpdateResult:
    """Switch one bundled MCP server on or off, and/or set its declared env, in the global ``mcp.json``.

    Honest about what actually happened:

    - **enable** — persisted, then attached live via :meth:`heim.tools.MCPBridge.add_server`, so its tools
      are callable on the very next chat turn (``applied=true``). If the spawn fails (e.g. an optional
      server whose extra isn't installed) the failure and its reason ride back instead of a fake success.
    - **disable** — persisted immediately, but a server already spawned at startup keeps its subprocess
      and its tools until ``heim serve`` restarts, so that answers ``restart_required=true``. Disabling one
      that was never spawned applies at once.
    - **env** — persisted, and applied in-process only while the server is still pending (spawning is
      lazy). A subprocess that is already running cannot be handed a new environment, so that is
      ``restart_required=true`` — the same refusal to claim a change that did not happen.

    404 for a name heim doesn't bundle; 400 for a variable the server never declared (the settings
    allow-list — a request can't inject arbitrary env into a spawned process); 409 when the change would
    require editing the project's own ``.mcp.json`` (committed and portable — heim never rewrites it from
    a web request), or when settings are set on a server that is switched off and so has no entry to hold
    them.
    """
    bridge: MCPBridge | None = getattr(request.app.state, "mcp_bridge", None)

    # A settings-only edit is the whole change; report it and stop. (Kept ahead of the toggle rather
    # than folded into it so the on/off path below reads exactly as it did before settings existed.)
    if body.enabled is None:
        if body.env is None:
            raise HTTPException(status_code=400, detail="nothing to change: pass 'enabled', 'env', or both")
        entry = _set_env_or_error(name, body.env)
        applied = _env_applied(bridge, entry)
        return McpUpdateResult(
            server=entry,
            applied=applied,
            restart_required=not applied,
            detail="" if applied else _STALE_ENV,
        )

    # Both: settings first, so the enable below spawns the server *with* them. When the server is
    # already running the write can't reach it, and `stale_env` downgrades the toggle's answer —
    # switching a running server "on" again is a no-op, so its `applied=True` would hide that.
    stale_env = False
    if body.env is not None:
        stale_env = not _env_applied(bridge, _set_env_or_error(name, body.env))

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

    spec = mcp_catalog.to_server(entry)
    result: McpUpdateResult
    if body.enabled:
        if not entry.enabled:
            # Written to the global file, yet still not resolved: the project's .mcp.json carries a
            # disable marker for this name, which wins. A restart wouldn't help — say so.
            result = McpUpdateResult(
                server=entry,
                applied=False,
                restart_required=False,
                detail=f"this project's {mcpservers.project_path()} disables {name!r}; remove that entry to use it",
            )
        elif bridge is None:  # no bridge (state-poking tests / a non-serve host) — can't attach in-process
            result = McpUpdateResult(
                server=entry, applied=False, restart_required=True, detail="restart heim serve to attach its tools"
            )
        else:
            attached, reason = await bridge.add_server(spec)
            result = McpUpdateResult(
                server=entry,
                applied=attached,
                # A spawn failure is not fixed by restarting — the command is missing or broken. Say why
                # instead, and let the install hint (surfaced by /api/mcp/servers) do the explaining.
                restart_required=False,
                detail="" if attached else f"could not start {name}: {reason}",
            )
    else:
        live = bridge is not None and bridge.is_live(spec)
        result = McpUpdateResult(
            server=entry,
            applied=not live,
            restart_required=live,
            detail="already running - its tools stay attached until heim serve restarts" if live else "",
        )
    if stale_env and result.applied:  # the toggle took, the settings didn't — the weaker answer wins
        return result.model_copy(update={"applied": False, "restart_required": True, "detail": _STALE_ENV})
    return result
