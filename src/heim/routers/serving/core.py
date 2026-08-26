"""Serving API: status, library listing, tags, model card, doctor, tools, and unload."""

import asyncio
import json
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel

from heim import capabilities, cards, doctor, mcp_catalog
from heim import library as library_ops
from heim import tags as tags_ops
from heim.config import Settings
from heim.routers.serving._base import (  # shared router + request deps
    ConfDep,
    LockDep,
    ManagerDep,
    _reject_if_generating,
    router,
)
from heim.runtime import sampling
from heim.runtime.sampling import ModelSampling
from heim.server import ServerManager, UpstreamManager
from heim.tools import MCPBridge, MCPToolset, TargetRouting


class ServerStatus(BaseModel):
    """Current runtime status for the UI."""

    state: str
    model: str | None = None
    locked: bool = False
    n_ctx: int | None = None  # context window the current model was loaded with (None = runtime default)
    error: str | None = None  # why the runtime died (stderr tail), if it exited unexpectedly
    default_system_prompt: str = ""  # the project (heim.toml) system prompt, so the UI can prefill/show it
    project_model: str | None = None  # the project's bound model, so the UI auto-loads it on open
    default_chat_voice: str | None = None  # the project's [project] chat_voice, so the UI defaults the Listen voice
    voice_enabled: bool = True  # the project's [voice] enabled; false hides the Voice surface (text-only assistant)
    runtime_load_timeout: int = 600  # seconds a load may take, so the UI polls as long as the runtime does
    default_max_tokens: int = 4096  # the cap applied when a request omits max_tokens (0 = unbounded)


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
    manager: ServerManager | UpstreamManager,
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
    if isinstance(manager, UpstreamManager) and request.app.state.active_generations > 0:
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
    on — heim's agent loop supplies tools server-side regardless of the remote.
    """
    if isinstance(manager, UpstreamManager):
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
    still runs under heim's sampling defaults — report those rather than 404ing, so a
    client can show the values that will actually be sent.
    """
    matches = library_ops.find(name)
    if not matches:
        if isinstance(manager, UpstreamManager):
            return ModelCardInfo(
                name=name, model_format="remote", size_human="—", path="", sampling=sampling.defaults()
            )
        raise HTTPException(status_code=404, detail=f"No library model matches {name!r}")
    m = matches[0]
    card_text: str | None = None
    card_path = cards.find_card(m.path) or (m.path / cards.SIDECAR_DIR / "model-card.md")
    if card_path.is_file():
        try:
            card_text = card_path.read_text(errors="replace")[:100_000]  # cap huge READMEs
        except OSError:
            card_text = None
    metadata: dict[str, Any] | None = None
    meta_path = m.path / cards.SIDECAR_DIR / "metadata.json"
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


def _mcp_checks(
    toolset: MCPToolset | None,
    bridge: MCPBridge | None = None,
    routing: TargetRouting | None = None,
) -> list[doctor.Check]:
    """Health checks for the project's MCP servers: live ones (tool counts), failed ones, and deferred ones.

    A server the project lists but couldn't start (e.g. optional ``web`` without ``make
    install-web``) shows as a warning with its install hint — instead of silently reporting
    0 tools — mirroring what ``heim project show`` does on the CLI.

    Lazily-deferred servers (a non-primary scoped target's own servers, spawned on that target's first
    use) are otherwise invisible until spawned: each still-pending prefix gets an informational row so the
    report discloses the whole configured surface, not just what is live. A pending prefix whose earlier
    spawn attempt failed is already covered by the error rows, so it's not double-listed here.
    """
    if toolset is None:
        return []
    checks: list[doctor.Check] = []
    counts: dict[str, int] = {}
    for schema in toolset.schemas:
        server = schema["function"]["name"].split("__")[0]
        counts[server] = counts.get(server, 0) + 1
    for server, n in counts.items():
        checks.append(doctor.Check(name=f"MCP: {server}", status=doctor.CheckStatus.ok, detail=f"{n} tool(s)"))
    error_labels = {label for label, _ in toolset.errors}
    for label, reason in toolset.errors:
        hint = mcp_catalog.optional_hint(label)
        checks.append(
            doctor.Check(
                name=f"MCP: {label}",
                # A known-optional server that isn't installed is a warning (fixable), not a hard fail.
                status=doctor.CheckStatus.warn if hint else doctor.CheckStatus.fail,
                detail=reason,
                hint=hint,
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
                    name=f"MCP: {prefix}",
                    status=doctor.CheckStatus.ok,
                    detail=f"deferred - spawns on first use{suffix}",
                )
            )
    return checks


@router.get("/api/doctor")
def doctor_report(settings: ConfDep, request: Request) -> doctor.DoctorReport:
    """System health: runtime binaries, library, the current project, and its MCP servers.

    Sync (``def``) so the filesystem scan runs in a worker thread, off the loop.
    Mirrors the ``heim doctor`` CLI so the UI can show the same status. The MCP section reports both the
    **live** servers (tool counts / failures) and any **deferred** ones — a non-primary scoped target's
    servers that spawn on first use — so a lazily-pending server is disclosed here before it's ever used.
    """
    report = doctor.run_checks(settings)
    state = request.app.state
    toolset: MCPToolset | None = getattr(state, "toolset", None)
    bridge: MCPBridge | None = getattr(state, "mcp_bridge", None)
    routing: TargetRouting | None = getattr(state, "target_routing", None)
    return doctor.DoctorReport(checks=[*report.checks, *_mcp_checks(toolset, bridge, routing)])


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
