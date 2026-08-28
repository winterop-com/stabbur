"""MCP server discovery + on/off toggles — the Tools panel's backing API.

``/api/tools`` answers "what can the agent call right now", which is empty on a fresh machine and
says nothing about the twelve ``stabbur-mcp-*`` servers stabbur ships. These routes answer the other half:
**what stabbur could run, and what is switched on** — so a client can render the whole shipped set with
checkboxes instead of the dead end that an empty tool list is.

They also answer the follow-up question a checkbox alone leaves hanging: **what is this server configured
to do**. A bundled server declares the env it reads, and the GET reports both what is persisted and the
value in force (an unset ``STABBUR_FILES_ROOT`` resolved to the absolute directory ``stabbur serve`` runs in) —
so a card can say "rooted at /Users/me/dev/thing" instead of "a configured workspace root", and the POST
can change it without sending anyone to hand-edit JSON.

The GET lists the third-party servers an ``mcp.json`` configures too (``bundled=false``), so the panel
sees the same set ``stabbur mcp list`` does — a server a user added themselves contributes tools to every
answer, and being invisible in the browser while visible in the CLI made the two surfaces disagree about
one file. Those rows are read-only here; the POST is unchanged, an allow-list over the shipped set.

The toggle writes the machine-global ``~/.config/stabbur/mcp.json`` (see :func:`stabbur.mcp_catalog.set_enabled`),
the same file ``stabbur mcp add --global`` writes, and is an **allow-list over the bundled set** — a name stabbur
doesn't ship is a 404, so no request can ever make stabbur spawn an arbitrary command. Restart semantics are
reported per call rather than assumed: an *enable* attaches live through the bridge's still-open exit stack;
a *disable* persists immediately but an already-spawned server keeps its subprocess (and its tools) until
``stabbur serve`` restarts, which the response says outright rather than pretending the tools are gone.
"""

from pathlib import Path

from fastapi import HTTPException, Request
from pydantic import BaseModel

from stabbur import mcp_catalog, mcpservers
from stabbur.models import BundledMcp
from stabbur.routers.serving._base import router
from stabbur.tools import MCPBridge


class McpUpdateRequest(BaseModel):
    """A change to one bundled server: its on/off state, its declared env settings, or both.

    Both fields are optional so a client sends only what it is changing — a settings edit must not
    have to restate ``enabled`` (and risk flipping it from a stale row).

    ``{"env": {...}, "enabled": true}`` is one call that switches a server on **and** configures it:
    the enable is persisted first (settings live *inside* the server's ``mcp.json`` entry, so writing
    them first is what the 409 "switch it on before configuring it" refusal is about), then the env,
    and only then is the server spawned — with the new settings in hand. The reverse combination,
    ``enabled: false`` with ``env``, is refused: switching off deletes the very entry the settings
    would live in, so honoring both would report a write that no longer exists.
    """

    enabled: bool | None = None
    env: dict[str, str] | None = None  # declared variables only; "" clears one back to its default


class McpUpdateResult(BaseModel):
    """The outcome of a change: the new persisted state, plus whether it is live *yet*."""

    server: BundledMcp
    applied: bool  # the change is in effect in this running server right now
    restart_required: bool  # `stabbur serve` must restart before the change takes effect
    detail: str = ""  # human-readable why, when applied is False or a caveat applies


_STALE_ENV = "already running - restart stabbur serve to apply the new settings"


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


async def _env_applied(bridge: MCPBridge | None, entry: BundledMcp) -> bool:
    """Whether the just-persisted settings are in force in *this* process.

    No bridge means no MCP subprocess exists here at all (a non-serve host / a state-poking test), so
    the file is the whole truth; otherwise only a not-yet-spawned server can pick the change up —
    decided under the bridge's per-server lock, so the answer can't be overtaken by a spawn in flight.
    """
    return bridge is None or await bridge.update_server(mcp_catalog.to_server(entry))


def _catalog_or_500(project_dir: Path | None = None) -> list[mcp_catalog.McpServerRow]:
    """The full catalogue, turning an unparseable ``mcp.json`` into a readable error, not a bare 500."""
    try:
        return mcp_catalog.catalog(project_dir)
    except mcpservers.McpConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/mcp/servers")
def list_mcp_servers(request: Request) -> list[mcp_catalog.McpServerRow]:
    """Every MCP server stabbur ships **plus** every third-party one ``mcp.json`` configures.

    Sync (``def``) so the config reads run in a worker thread, off the loop. ``enabled`` is the resolved
    truth (global + project, honoring a project override or disable marker) — i.e. what stabbur spawns —
    and ``scope`` names the file that switches it on, so a client can explain why a server it can't
    disable globally is on. A server whose optional extra isn't installed is listed with
    ``installed=false`` and its install hint rather than hidden.

    ``bundled=false`` marks a server stabbur doesn't ship: listed because it is spawned and its tools are
    callable (``stabbur mcp list`` has always shown it), but **not** togglable through the POST below,
    which stays an allow-list over the shipped set. ``live``/``tools`` report what is attached in *this*
    process — an enabled server that never started reads ``live=false``, which is the difference between
    "configured" and "working"; both are ``null`` when there is no bridge to ask.

    ``env`` is what the resolving ``mcp.json`` entry persists (usually nothing) and ``settings`` is the
    server's declared knobs, each carrying the value **actually in force** — the configured one, else the
    resolved default. Both are filled in whether or not the server is on, because "which directory can it
    see" is a question asked before switching it on as often as after.
    """
    rows = _catalog_or_500()
    bridge: MCPBridge | None = getattr(request.app.state, "mcp_bridge", None)
    if bridge is None:
        return rows  # no MCP process here at all: live/tools stay null rather than guess "off"
    live: list[mcp_catalog.McpServerRow] = []
    for row in rows:
        spec = mcp_catalog.to_server(row)
        live.append(row.model_copy(update={"live": bridge.is_live(spec), "tools": bridge.tool_count(spec)}))
    return live


@router.post("/api/mcp/servers/{name}")
async def set_mcp_server(name: str, body: McpUpdateRequest, request: Request) -> McpUpdateResult:
    """Switch one bundled MCP server on or off, and/or set its declared env, in the global ``mcp.json``.

    Honest about what actually happened:

    - **enable** — persisted, then attached live via :meth:`stabbur.tools.MCPBridge.add_server`, so its tools
      are callable on the very next chat turn (``applied=true``). If the spawn fails (e.g. an optional
      server whose extra isn't installed) the failure and its reason ride back instead of a fake success.
    - **disable** — persisted immediately, but a server already spawned at startup keeps its subprocess
      and its tools until ``stabbur serve`` restarts, so that answers ``restart_required=true``. Disabling one
      that was never spawned applies at once.
    - **env** — persisted, and applied in-process only while the server is still pending (spawning is
      lazy). A subprocess that is already running cannot be handed a new environment, so that is
      ``restart_required=true`` — the same refusal to claim a change that did not happen.

    404 for a name stabbur doesn't bundle; 400 for a variable the server never declared (the settings
    allow-list — a request can't inject arbitrary env into a spawned process), and for ``enabled: false``
    sent together with ``env`` (switching off removes the entry the settings would live in); 409 when the
    change would require editing the project's own ``.mcp.json`` (committed and portable — stabbur never
    rewrites it from a web request), or when settings are set on a server that is switched off and so has
    no entry to hold them.
    """
    bridge: MCPBridge | None = getattr(request.app.state, "mcp_bridge", None)

    # A settings-only edit is the whole change; report it and stop. (Kept ahead of the toggle rather
    # than folded into it so the on/off path below reads exactly as it did before settings existed.)
    if body.enabled is None:
        if body.env is None:
            raise HTTPException(status_code=400, detail="nothing to change: pass 'enabled', 'env', or both")
        entry = _set_env_or_error(name, body.env)
        applied = await _env_applied(bridge, entry)
        return McpUpdateResult(
            server=entry,
            applied=applied,
            restart_required=not applied,
            detail="" if applied else _STALE_ENV,
        )
    if body.env is not None and not body.enabled:
        # Switching off deletes the mcp.json entry; settings written on the way out would vanish with it,
        # so accepting both would report a write that no longer exists. Refuse instead of lying.
        raise HTTPException(
            status_code=400,
            detail=(
                f"cannot set settings while switching {name!r} off — "
                "they live in the mcp.json entry that switching off removes"
            ),
        )

    entry = _set_enabled_or_error(name, body.enabled)

    if not body.enabled:
        return await _disabled_result(bridge, entry)

    if not entry.enabled:
        # Written to the global file, yet still not resolved: the project's .mcp.json carries a
        # disable marker for this name, which wins. A restart wouldn't help — say so.
        detail = f"this project's {mcpservers.project_path()} disables {name!r}; remove that entry to use it"
        if body.env is not None:  # the env write was skipped with it — never imply it landed
            detail += " (its settings were not written)"
        return McpUpdateResult(server=entry, applied=False, restart_required=False, detail=detail)

    # Settings AFTER the enable: they live inside the server's mcp.json entry, which the enable is what
    # creates — writing them first is exactly the state set_env refuses with a 409, which turned the
    # documented one-call `{"env": ..., "enabled": true}` into an error with the enable silently dropped.
    # Still before the spawn below, so the server starts with them. `stale_env` downgrades the toggle's
    # answer for an already-running server: switching it "on" again is a no-op whose `applied=True`
    # would otherwise hide that the settings could not reach the live subprocess.
    stale_env = False
    if body.env is not None:
        entry = _set_env_or_error(name, body.env)
        stale_env = not await _env_applied(bridge, entry)

    if bridge is None:  # no bridge (state-poking tests / a non-serve host) — can't attach in-process
        result = McpUpdateResult(
            server=entry, applied=False, restart_required=True, detail="restart stabbur serve to attach its tools"
        )
    else:
        attached, reason = await bridge.add_server(mcp_catalog.to_server(entry))
        result = McpUpdateResult(
            server=entry,
            applied=attached,
            # A spawn failure is not fixed by restarting — the command is missing or broken. Say why
            # instead, and let the install hint (surfaced by /api/mcp/servers) do the explaining.
            restart_required=False,
            detail="" if attached else f"could not start {name}: {reason}",
        )
    if stale_env and result.applied:  # the toggle took, the settings didn't — the weaker answer wins
        return result.model_copy(update={"applied": False, "restart_required": True, "detail": _STALE_ENV})
    return result


def _set_enabled_or_error(name: str, enabled: bool) -> BundledMcp:
    """Persist one server's on/off state, turning each refusal into its own status code."""
    try:
        return mcp_catalog.set_enabled(name, enabled)
    except mcp_catalog.UnknownServer as exc:
        raise HTTPException(status_code=404, detail=f"{name!r} is not a bundled MCP server") from exc
    except mcp_catalog.ProjectScoped as exc:
        raise HTTPException(
            status_code=409,
            detail=f"{name!r} is switched on by this project's {exc} — edit it (or `stabbur mcp remove {name}`).",
        ) from exc
    except mcpservers.McpConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _disabled_result(bridge: MCPBridge | None, entry: BundledMcp) -> McpUpdateResult:
    """The outcome of a disable: applied unless its subprocess is already attached.

    Goes through :meth:`stabbur.tools.MCPBridge.remove_server` rather than a bare "is it live?" read, so
    the answer is decided under the bridge's per-server lock (a spawn in flight is waited out, not
    reported as absent) *and* a still-queued lazy spawn is cancelled — otherwise a server switched off
    here would still be spawned on some target's first use, attaching the tools just switched off.
    """
    live = bridge is not None and not await bridge.remove_server(mcp_catalog.to_server(entry))
    return McpUpdateResult(
        server=entry,
        applied=not live,
        restart_required=live,
        detail="already running - its tools stay attached until stabbur serve restarts" if live else "",
    )
