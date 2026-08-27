"""Serving API: status, library listing, tags, model card, doctor, tools, and unload."""

import asyncio
import json
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from stabbur import capabilities, cards, doctor, mcp_catalog
from stabbur import library as library_ops
from stabbur import tags as tags_ops
from stabbur.backends import Backends
from stabbur.config import Settings
from stabbur.routers.serving._base import (  # shared router + request deps
    ConfDep,
    LockDep,
    ManagerDep,
    _reject_if_generating,
    router,
)
from stabbur.runtime import sampling
from stabbur.runtime.sampling import ModelSampling
from stabbur.tools import MCPBridge, MCPToolset, TargetRouting


class ServerStatus(BaseModel):
    """Current runtime status for the UI."""

    state: str
    model: str | None = None
    locked: bool = False
    n_ctx: int | None = None  # context window the current model was loaded with (None = runtime default)
    error: str | None = None  # why the runtime died (stderr tail), if it exited unexpectedly
    # Which backend the models actually run on: the remote's base URL under ``serve --upstream``,
    # None when stabbur spawns its own runtimes. Nothing else in this payload distinguishes the two —
    # a remote id looks like a local model name — so a UI that wants to say where a reply comes
    # from has no other way to know.
    upstream: str | None = None
    default_system_prompt: str = ""  # the project (stabbur.toml) system prompt, so the UI can prefill/show it
    project_model: str | None = None  # the project's bound model, so the UI auto-loads it on open
    default_chat_voice: str | None = None  # the project's [project] chat_voice, so the UI defaults the Listen voice
    voice_enabled: bool = True  # the project's [voice] enabled; false hides the Voice surface (text-only assistant)
    runtime_load_timeout: int = 600  # seconds a load may take, so the UI polls as long as the runtime does
    default_max_tokens: int = 4096  # the cap applied when a request omits max_tokens (0 = unbounded)
    # Stabbur's own sampling defaults — what a model that recommends nothing of its own runs under.
    # Reported so a settings UI can show the value actually in force for an untouched control
    # without keeping a second copy of the numbers (they drift); a *loaded* model's own
    # recommendation is the better answer where there is one, and comes from /api/model.
    default_sampling: ModelSampling = Field(default_factory=sampling.defaults)


class LibraryModelInfo(BaseModel):
    """A runnable library model, for the UI's model picker."""

    name: str
    model_format: str
    size_bytes: int
    size_human: str
    vision: bool = False
    audio: bool = False
    tools: bool = False
    context_length: int | None = None
    tags: list[str] = []


async def _status(
    manager: Backends,
    settings: Settings,
    system_prompt: str = "",
    project_model: str | None = None,
    chat_voice: str | None = None,
    voice_enabled: bool = True,
) -> ServerStatus:
    current = manager.current
    return ServerStatus(
        state=(await manager.state()).value,
        model=current.name if current else None,
        locked=settings.serve_model is not None,
        n_ctx=manager.n_ctx,
        error=manager.last_error if current is None else None,
        upstream=manager.base_url if manager.is_upstream else None,
        default_system_prompt=system_prompt,
        project_model=project_model,
        default_chat_voice=chat_voice,
        voice_enabled=voice_enabled,
        runtime_load_timeout=settings.runtime_load_timeout,
        default_max_tokens=settings.default_max_tokens,
    )


@router.get("/api/status")
async def status(manager: ManagerDep, settings: ConfDep, request: Request) -> ServerStatus:
    """Report the loaded model and runtime state."""
    # A generation actively streaming through this server IS proof the upstream is alive —
    # and a busy llama-server answers /v1/models slowly mid-generation, so probing it right
    # then is how a healthy backend reads as down. Skip the probe while tokens are flowing.
    if manager.is_upstream and request.app.state.active_generations > 0:
        manager.touch()
    return await _status(
        manager,
        settings,
        getattr(request.app.state, "system_prompt", "") or "",
        getattr(request.app.state, "project_model", None),
        getattr(request.app.state, "chat_voice", None),
        getattr(request.app.state, "voice_enabled", True),
    )


@router.get("/api/library")
def library(manager: ManagerDep, settings: ConfDep) -> list[LibraryModelInfo]:
    """List the models the UI's picker can load: the library, or the upstream's ids.

    Sync (``def``) so the filesystem scan (or the upstream probe) runs in a worker
    thread, off the loop. In upstream mode the rows are the remote's ``/v1/models``:
    format ``remote``, no size, vision/audio from the reported modalities, and a
    ``loaded`` tag marking what the remote has resident right now. ``tools`` is left
    on — stabbur's agent loop supplies tools server-side regardless of the remote.
    """
    if manager.is_upstream:
        try:
            rows = manager.models()
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return [
            LibraryModelInfo(
                name=r.name,
                model_format="remote",
                size_bytes=0,
                size_human="—",
                vision=r.vision,
                audio=r.audio,
                tools=True,
                tags=(["loaded"] if r.loaded else []),
            )
            for r in rows
        ]
    tag_maps: dict[str, dict[str, list[str]]] = {}  # cache tags.json per library root
    out: list[LibraryModelInfo] = []
    for m in library_ops.scan():
        if not m.generative or m.is_ollama:
            continue
        caps = capabilities.capabilities(m)
        key = str(m.library_root)
        if key not in tag_maps:
            tag_maps[key] = tags_ops.load(m.library_root)
        out.append(
            LibraryModelInfo(
                name=m.name,
                model_format=m.model_format.value,
                size_bytes=m.size_bytes,
                size_human=m.size_human,
                vision=caps.vision,
                audio=caps.audio,
                tools=caps.tools,
                context_length=caps.context_length,
                tags=tag_maps[key].get(m.name, []),  # tags come from the model's own library
            )
        )
    return out


class TagUpdate(BaseModel):
    """Set a model's tags (the full replacement list)."""

    model: str
    tags: list[str]


@router.post("/api/tags")
def set_model_tags(body: TagUpdate) -> TagUpdate:
    """Replace ``model``'s tags with ``tags`` (normalized + deduped). Returns them.

    Tags are written into the library the model lives in, so they travel with it.
    """
    matches = library_ops.find(body.model)
    if not matches:  # don't write a phantom tag entry for a model that isn't in the library
        raise HTTPException(status_code=404, detail=f"{body.model!r} is not in the library")
    saved = tags_ops.set_tags(matches[0].library_root, body.model, body.tags)
    return TagUpdate(model=body.model, tags=saved)


@router.get("/api/tags/registry")
def tag_registry() -> dict[str, tags_ops.TagMeta]:
    """The tag style registry (``{tag: {color, icon, description}}``) merged across libraries.

    First library in scope wins on a conflict. The UI prefers a registered color over the
    name-derived one, and renders the icon if present.
    """
    merged: dict[str, tags_ops.TagMeta] = {}
    for root in reversed(library_ops.roots()) if library_ops.configured() else []:
        merged.update(tags_ops.load_registry(root))  # earlier roots override (applied last)
    return merged


class ModelCardInfo(BaseModel):
    """A model's card + metadata for the UI's info panel."""

    name: str
    model_format: str
    size_human: str
    path: str
    card: str | None = None
    metadata: dict[str, Any] | None = None
    sampling: ModelSampling = ModelSampling()  # model-recommended defaults (for UI placeholders)


@router.get("/api/model")
def model_info(name: str, manager: ManagerDep) -> ModelCardInfo:
    """Return the model card (README/model-card.md) + metadata for a library model.

    A model served by an upstream has no library copy (so no card or metadata), but it
    still runs under stabbur's sampling defaults — report those rather than 404ing, so a
    client can show the values that will actually be sent.
    """
    matches = library_ops.find(name)
    if not matches:
        if manager.is_upstream:
            return ModelCardInfo(
                name=name, model_format="remote", size_human="—", path="", sampling=sampling.defaults()
            )
        raise HTTPException(status_code=404, detail=f"No library model matches {name!r}")
    m = matches[0]
    card_text: str | None = None
    card_path = cards.find_card(m.path) or (cards.sidecar_dir(m.path) / "model-card.md")
    if card_path.is_file():
        try:
            card_text = card_path.read_text(errors="replace")[:100_000]  # cap huge READMEs
        except OSError:
            card_text = None
    metadata: dict[str, Any] | None = None
    meta_path = cards.sidecar_dir(m.path) / "metadata.json"
    if meta_path.is_file():
        try:
            metadata = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            metadata = None
    return ModelCardInfo(
        name=m.name,
        model_format=m.model_format.value,
        size_human=m.size_human,
        path=str(m.path),
        card=card_text,
        metadata=metadata,
        sampling=sampling.recommended(m),
    )


class ToolInfo(BaseModel):
    """One MCP tool exposed to the UI (namespaced ``<server>__<tool>``)."""

    name: str
    server: str
    tool: str
    description: str


def _pending_owners(prefix: str, routing: TargetRouting | None) -> list[str]:
    """The target ids that explicitly own a pending ``prefix`` (empty without routing).

    A pending prefix is only ever a non-primary scoped target's own server (shared servers are always
    eager, hence never pending), so its owners are the targets whose explicit set names it — surfaced so
    the doctor row says which target's first use will spawn it.
    """
    if routing is None:
        return []
    return sorted(tid for tid, prefixes in routing.explicit.items() if prefix in prefixes)


_TOOLS_SHOWN = 3
"""How many tool names a server's health row names before summarising the rest."""


def _tool_summary(names: list[str]) -> str:
    """Name a server's first few tools, then count the rest.

    A bare "13 tool(s)" says how much a server brought without saying what any of it is, which is
    no help to someone opening health to find out whether the thing they want is attached. The
    names are what answer that; the tail is a count because the row is one line in a narrow
    flyout, and the full list with its per-chat switches already lives in the chat settings panel.
    """
    shown = sorted(names)[:_TOOLS_SHOWN]
    rest = len(names) - len(shown)
    return ", ".join(shown) + (f", +{rest} more" if rest > 0 else "")


def _mcp_checks(
    toolset: MCPToolset | None,
    bridge: MCPBridge | None = None,
    routing: TargetRouting | None = None,
) -> list[doctor.Check]:
    """Health checks for the project's MCP servers: live ones (tool counts), failed ones, and deferred ones.

    A server the project lists but couldn't start (e.g. optional ``web`` without ``make
    install-web``) shows as a warning with its install hint — instead of silently reporting
    0 tools — mirroring what ``stabbur project show`` does on the CLI.

    Lazily-deferred servers (a non-primary scoped target's own servers, spawned on that target's first
    use) are otherwise invisible until spawned: each still-pending prefix gets an informational row so the
    report discloses the whole configured surface, not just what is live. A pending prefix whose earlier
    spawn attempt failed is already covered by the error rows, so it's not double-listed here.

    Every row here is a **child** of the ``Tools (MCP)`` summary check (``group``), so a consumer nests
    them under it instead of listing them as its siblings. That also lets the rows be named for the server
    alone: the old ``MCP: datetime`` prefix existed only to say which family a flat row belonged to, and
    under a parent that already says "Tools (MCP)" it's noise repeated once per server.
    """
    if toolset is None:
        return []
    checks: list[doctor.Check] = []
    tools: dict[str, list[str]] = {}
    for schema in toolset.schemas:
        server, _, tool = schema["function"]["name"].partition("__")
        tools.setdefault(server, []).append(tool or schema["function"]["name"])
    for server, names in tools.items():
        checks.append(
            doctor.Check(name=server, status=doctor.CheckStatus.ok, detail=_tool_summary(names), group=doctor.MCP_GROUP)
        )
    error_labels = {label for label, _ in toolset.errors}
    for label, reason in toolset.errors:
        hint = mcp_catalog.optional_hint(label)
        checks.append(
            doctor.Check(
                name=label,
                # A known-optional server that isn't installed is a warning (fixable), not a hard fail.
                status=doctor.CheckStatus.warn if hint else doctor.CheckStatus.fail,
                detail=reason,
                hint=hint,
                group=doctor.MCP_GROUP,
            )
        )
    if bridge is not None:
        for prefix in sorted(bridge.pending_prefixes):
            # A prior spawn attempt that failed left the server pending AND recorded an error under its
            # label — that error row already tells the story, so don't also emit a "deferred" row for it.
            pending_label = bridge.pending_label(prefix)
            if pending_label is not None and pending_label in error_labels:
                continue
            owners = _pending_owners(prefix, routing)
            suffix = f" (target {', '.join(owners)})" if owners else ""
            checks.append(
                doctor.Check(
                    name=prefix,
                    status=doctor.CheckStatus.ok,
                    detail=f"deferred - spawns on first use{suffix}",
                    group=doctor.MCP_GROUP,
                )
            )
    return checks


def _loaded_model(manager: Backends) -> doctor.LoadedModel:
    """What this server has resident, for the doctor's ``Model`` row.

    The one fact ``stabbur doctor`` cannot see for itself: the CLI has no runtime, so it reports the
    model that *would* load, while a serving stabbur knows the one that did. ``last_error`` is only the
    story when nothing is current — a live model plus a stale error from a previous crash is a
    healthy server, and the row should say so.
    """
    current = manager.current
    if current is None:
        return doctor.LoadedModel(error=manager.last_error)
    return doctor.LoadedModel(name=current.name, n_ctx=manager.n_ctx)


@router.get("/api/doctor")
def doctor_report(manager: ManagerDep, settings: ConfDep, request: Request) -> doctor.DoctorReport:
    """System health: the backend, the loaded model, runtimes, library, the project, and its MCP servers.

    Sync (``def``) so the filesystem scan runs in a worker thread, off the loop.
    Mirrors the ``stabbur doctor`` CLI so the UI can show the same status, plus the one thing the CLI can't
    know — which model this server actually has loaded. The MCP section reports both the
    **live** servers (tool counts / failures) and any **deferred** ones — a non-primary scoped target's
    servers that spawn on first use — so a lazily-pending server is disclosed here before it's ever used.
    Those rows are children of the ``Tools (MCP)`` check (see ``Check.group``).
    """
    report = doctor.run_checks(settings, loaded=_loaded_model(manager))
    state = request.app.state
    toolset: MCPToolset | None = getattr(state, "toolset", None)
    bridge: MCPBridge | None = getattr(state, "mcp_bridge", None)
    routing: TargetRouting | None = getattr(state, "target_routing", None)
    mcp_rows = _mcp_checks(toolset, bridge, routing)
    checks = [*report.checks, *mcp_rows]
    # This server's live toolset can hold servers the CWD's mcp.json doesn't (a --mcp layered on at
    # launch, another target's own servers), so check_project may not have emitted the parent those
    # rows nest under. Never leave a child orphaned by a missing group: synthesize the summary row.
    if mcp_rows and not any(c.name == doctor.MCP_GROUP for c in checks):
        parent = doctor.Check(
            name=doctor.MCP_GROUP,
            status=doctor.CheckStatus.ok,
            detail=f"{len(mcp_rows)} server{'' if len(mcp_rows) == 1 else 's'}",
        )
        checks.insert(len(report.checks), parent)
    return doctor.DoctorReport(checks=checks)


@router.get("/api/tools")
def tools(request: Request) -> list[ToolInfo]:
    """List the MCP tools **currently live** on this server (empty if none configured).

    Live only: a non-primary target's servers are spawned lazily on first use (its first chat turn /
    ``?verify=1`` / bind), so a lazily-pending target's tools appear here only after that first use.
    This is deliberately not a merged "declared" view — it reflects what the agent loop can call right now.
    """
    toolset: MCPToolset | None = getattr(request.app.state, "toolset", None)
    if toolset is None:
        return []
    out: list[ToolInfo] = []
    for schema in toolset.schemas:
        fn = schema["function"]
        name = fn["name"]
        server, _, tool = name.partition("__")
        # Descriptions come from tool docstrings; strip backtick markup (RST/markdown
        # inline literals) so it reads as prose in the UI (rendered as plain text).
        desc = fn.get("description", "").replace("`", "")
        out.append(ToolInfo(name=name, server=server or "mcp", tool=tool or name, description=desc))
    return out


@router.post("/api/unload")
async def unload(manager: ManagerDep, settings: ConfDep, lock: LockDep, request: Request) -> ServerStatus:
    """Eject the loaded model, stopping its runtime process (frees memory).

    Rejected in locked mode (the server is bound to one model). A no-op if
    nothing is loaded.
    """
    if settings.serve_model is not None:
        raise HTTPException(status_code=409, detail="Server is locked to a single model")
    # terminate can wait up to 10s — keep it off-loop; the lock serializes it
    # against a concurrent load so they don't fight over the process handle.
    async with lock:
        _reject_if_generating(request)  # don't kill the runtime under a live generation
        await asyncio.to_thread(manager.stop)
    return await _status(manager, settings)
