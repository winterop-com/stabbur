"""Assistant metadata API: echo the project's assistant target(s) for UI clients.

stabbur stays domain-generic — it never interprets these fields. A project can declare **one** target
(``[assistant]``) or **many** (``[[assistants]]``, e.g. one DHIS2 instance per path on an origin).

- ``GET /api/assistants`` returns the sanitized registry (every target); ``?url=<tabUrl>`` adds which
  target a browser tab falls under (``selected`` / ``matches`` via :func:`stabbur.targets.select`).
- ``GET /api/assistants/{id}`` echoes one target; ``?verify=1`` runs its verify tool (cached 60s per id).
- ``POST /api/assistants/{id}/verify`` is the same probe under the method that admits it *runs* something
  (it can spawn the target's MCP servers and calls a tool); the ``?verify=`` GET is kept for shipped
  clients and is guarded as a mutating call.
- ``POST /api/assistants/{id}/bind|unbind`` runs that target's bind recipe.
- ``GET /api/assistant`` (+ ``?verify=1``) and ``POST /api/assistant/bind|unbind`` are the **compat**
  single-target routes for old clients — they read ``app.state.assistant`` (the registry's primary) and
  route through the *same* per-id verify cache keyed by the primary's id, so a rate-limited instance is
  probed once whether the caller hits ``/api/assistant`` or ``/api/assistants/{primary}``.

Sanitized echo: a target's ``verify`` spec and a bind mode's ``command`` / ``secret_env`` are server-side
execution details, never surfaced; ``probe`` rides through verbatim (it's *for* the client to run) and the
bind echo carries only the mint recipe, mode *names*, and per-mode ``unbind_note``.
"""

import asyncio
import os
import signal
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from stabbur.project import (
    ALLOWED_COMMAND_PLACEHOLDERS,
    COMMAND_PLACEHOLDER_RE,
    AssistantInfo,
    AssistantVerify,
    BindMode,
    _slugify,
)
from stabbur.routers.serving._base import router
from stabbur.targets import AssistantRegistry, select, selected
from stabbur.tools import MCPBridge, MCPToolset, TargetRouting

_VERIFY_TTL = 60.0  # seconds a verify outcome is cached, so ?verify=1 polling doesn't re-probe each call
_MAX_SECRET = 16384  # cap on a bind secret so a caller can't shove an unbounded blob into the process env
_MAX_OUTPUT = 16384  # cap on each of a mode's captured stdout/stderr (bounds RAM, redaction, response)


def _capture(raw: bytes, secrets: tuple[str | None, ...] = ()) -> str:
    """Decode captured child output, redact the secrets, then cap what is returned.

    Order matters, and it used to be the other way round: capping first meant a secret straddling
    the cut kept its leading half in the response, because the redaction pass no longer had the
    whole string to match. Redaction now runs over the entire output — ``communicate()`` has
    already buffered all of it in memory by the time this is called, so scanning it whole costs no
    bound the process wasn't holding anyway — and the cap then bounds the JSON response, with a
    marker so the caller can tell the output was cut. Encoded copies of a secret are still not
    covered (only literal occurrences are).
    """
    text = raw.decode(errors="replace")
    for value in secrets:
        if value:
            text = text.replace(value, "***")
    if len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + "... [truncated]"
    return text


def _killpg(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the child's whole process group (it was spawned with ``start_new_session``).

    Mirrors :mod:`stabbur.runtime.supervisor`: a plain ``proc.kill()`` signals only the direct child, so
    a grandchild it forked — still holding the secret in its env — would be orphaned, not reaped.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


class AssistantVerified(BaseModel):
    """Outcome of running the project's verify tool against the target instance."""

    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    checked_at: float


class AssistantResponse(BaseModel):
    """One target's metadata, echoed for UI clients (stabbur never interprets it).

    Mirrors :class:`stabbur.project.AssistantInfo`'s public fields minus ``verify`` (an execution
    detail, never surfaced); ``extra="allow"`` lets a project's unknown keys ride along. ``probe`` is
    echoed verbatim (it's *for* the client to run). ``bind`` is echoed sanitized: the browser-side mint
    recipe plus only the mode *names* — a mode's argv / secret_env are server-side execution details.
    In a multi-target list ``id`` is the registry's collision-safe id for the target (``registry.ids``).
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    base_url: str | None = None
    auth: str | None = None
    readonly: bool | None = None
    source: str | None = None
    mcp_servers: list[str] = Field(default_factory=list)
    can_verify: bool = False
    verified: AssistantVerified | None = None
    probe: dict[str, Any] | None = None
    can_bind: bool = False
    bind: dict[str, Any] | None = None


class AssistantListResponse(BaseModel):
    """The sanitized multi-target registry. ``?url=`` adds ``selected`` / ``matches`` for a tab URL.

    Without ``?url=`` only ``targets`` is serialized (the endpoint drops the selection keys); with it,
    ``matches`` is every target id the tab falls under (most-specific first) and ``selected`` is the
    unique strictly-highest-rank pick — set when one target is more specific than the rest, ``null`` on a
    real top-rank tie or no match, so the client auto-picks or shows a picker.
    """

    model_config = ConfigDict(extra="allow")

    targets: list[AssistantResponse] = Field(default_factory=list)
    selected: str | None = None
    matches: list[str] | None = None


class BindRequest(BaseModel):
    """A request to install a bound credential: which mode to run, and the secret(s) to hand it."""

    mode: str
    secret: str
    extra_secret: str | None = None
    """An optional secondary secret (e.g. a CSRF token alongside a session cookie), handed to the
    mode's ``extra_secret_env`` when it declares one. Same size cap as ``secret``; never placed on the
    argv or in logs (redacted from captured output). Ignored when the mode has no ``extra_secret_env``."""


class UnbindRequest(BaseModel):
    """A request to reverse a bound credential: which mode's ``unbind_command`` to run."""

    mode: str


class BindResult(BaseModel):
    """The outcome of running a bind/unbind mode's argv (secret redacted from captured output)."""

    ok: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""


def _tool_available(toolset: MCPToolset, tool: str) -> bool:
    """Whether ``tool`` resolves in ``toolset`` — defensive so a stub toolset (tests) still works."""
    try:
        return tool in toolset.names
    except Exception:  # noqa: BLE001 - a toolset that can't list its names simply can't verify
        return False


def _can_verify(
    info: AssistantInfo,
    toolset: MCPToolset | None,
    bridge: MCPBridge | None,
    routing: TargetRouting | None,
    target_id: str | None,
) -> bool:
    """Whether a live ``?verify=1`` probe is *possible* for this target — honest about lazy bridges.

    A verify tool that is already live counts (the eager case). For a **lazily-deferred** target the tool
    isn't attached yet, so fall back to the DECLARED config: the verify tool's server prefix is pending on
    the bridge *and* owned by this target (its explicit set, or everything for an owns-all target). The
    verify call itself triggers the spawn, so reporting ``can_verify=true`` here isn't a lie — it's what
    will succeed once first use spawns the bridge. Without a bridge/routing (state-poking tests) this is
    exactly the old live-only check.
    """
    spec = info.verify
    if spec is None or toolset is None:
        return False
    if _tool_available(toolset, spec.tool):
        return True
    if bridge is None or routing is None or target_id is None:
        return False
    prefix = spec.tool.split("__", 1)[0]
    if prefix not in bridge.pending_prefixes:
        return False
    # Accepted footgun: a server that is *permanently* unspawnable (a bad command that fails every
    # first-use attempt) stays pending forever, so this keeps returning True. That's deliberate — pending
    # is retryable by design (a transient failure must not latch can_verify off), the verify call itself
    # then returns a clean ok=False data state rather than lying, and once an attempt has run its failure
    # is visible in /api/doctor's MCP rows. We don't demote can_verify on a prior failure here.
    if target_id in routing.owns_all:
        return True
    return prefix in routing.explicit.get(target_id, set())


def _coerce(result: Any) -> dict[str, Any]:
    """Normalize a verify tool's result into a JSON object for the response."""
    if isinstance(result, BaseModel):
        return result.model_dump()
    if isinstance(result, dict):
        return result
    return {"text": str(result)}


def _bind_echo(info: AssistantInfo) -> tuple[dict[str, Any] | None, bool]:
    """The sanitized bind echo (mint recipe + mode names + unbind notes) and whether a bind is runnable.

    Excludes every execution detail: a mode's ``command`` / ``secret_env`` never leave the server; only
    the mode *names* (sorted) and any user-facing ``unbind_note`` (keyed by mode) ride out, mirroring the
    ``verify`` exclusion. ``can_bind`` is true when at least one mode is declared.
    """
    if info.bind is None:
        return None, False
    echo = info.bind.model_dump(exclude={"modes"}, exclude_none=True)
    echo["modes"] = sorted(info.bind.modes)
    # A mode's user-facing unbind_note is guidance for the client (not an execution detail like
    # command / secret_env), so surface it keyed by mode. Plucked explicitly so a mode's extra="allow"
    # fields can never ride into the echo — only the note, keyed by mode name.
    unbind_notes = {name: mode.unbind_note for name, mode in info.bind.modes.items() if mode.unbind_note}
    if unbind_notes:
        echo["unbind_notes"] = unbind_notes
    return echo, bool(info.bind.modes)


def _sanitize(
    info: AssistantInfo,
    toolset: MCPToolset | None,
    *,
    target_id: str | None = None,
    verified: AssistantVerified | None = None,
    bridge: MCPBridge | None = None,
    routing: TargetRouting | None = None,
) -> AssistantResponse:
    """Build one target's sanitized echo (verify internals + bind argv/secret_env excluded).

    Shared by the compat single-target route and the multi-target list/detail routes. ``can_verify``
    reports whether a live probe is possible (a verify spec is declared and its tool is attached, or —
    for a lazily-deferred target — its verify server is a pending bridge the probe would spawn; see
    :func:`_can_verify`). ``target_id`` (when given) overrides the info's own computed ``id`` with the
    registry's collision-safe id. Built as a dict (not ``**kwargs``) so a project extra key named after a
    response field (``can_verify`` / ``verified`` / ``can_bind`` / ``bind``) is overridden, not a ``TypeError``.
    """
    base = info.model_dump(exclude={"verify", "bind"}, exclude_none=True)
    can_verify = _can_verify(info, toolset, bridge, routing, target_id)
    bind_echo, can_bind = _bind_echo(info)
    data: dict[str, Any] = {
        **base,
        "can_verify": can_verify,
        "verified": verified,
        "can_bind": can_bind,
        "bind": bind_echo,
    }
    if target_id is not None:
        data["id"] = target_id
    return AssistantResponse.model_validate(data)


async def _run_verify(toolset: MCPToolset | None, spec: AssistantVerify) -> AssistantVerified:
    """Run the verify tool once (no caching). Any failure is a data state (``ok=False``), never raised.

    An unknown tool, a timeout, or no toolset all report ``ok=False`` with the message so the caller
    still returns HTTP 200 — stabbur doesn't know what "connected" means for the domain, only whether the
    project-declared probe succeeded.
    """
    try:
        if toolset is None:
            raise RuntimeError("no MCP tools available to verify")
        result = await toolset.call_structured(spec.tool, spec.args, timeout=spec.timeout)
        return AssistantVerified(ok=True, data=_coerce(result), checked_at=time.time())
    except KeyError:  # call_structured raises KeyError(name); its str() is just "'name'"
        return AssistantVerified(ok=False, error=f"verify tool not attached: {spec.tool}", checked_at=time.time())
    except Exception as exc:  # noqa: BLE001 - a verify failure is reported as state, never raised
        return AssistantVerified(ok=False, error=str(exc), checked_at=time.time())


# --- Per-target verify cache (app.state.assistant_verified_by_id, one slot per registry id) -----------
# Each target id gets its own (checked_at, AssistantVerified) slot and its own single-flight lock. The
# locks live in one dict created lazily; no guard lock is needed because inserting a lock has no await
# between the .get() and the assignment, so on the single-threaded event loop two coroutines can't both
# create one (the second sees the first's entry). Verifying / binding one target never touches another.


def _fresh_target(state: Any, target_id: str) -> AssistantVerified | None:
    """The per-id cached verify outcome for ``target_id``, if one exists and is within the TTL."""
    cache: dict[str, tuple[float, AssistantVerified]] = getattr(state, "assistant_verified_by_id", None) or {}
    entry = cache.get(target_id)
    if entry is not None and time.time() - entry[0] < _VERIFY_TTL:
        return entry[1]
    return None


def _verify_lock(state: Any, target_id: str) -> asyncio.Lock:
    """``target_id``'s single-flight verify lock, created on first use."""
    locks: dict[str, asyncio.Lock] | None = getattr(state, "assistant_verify_locks", None)
    if locks is None:
        locks = {}
        state.assistant_verify_locks = locks
    lock = locks.get(target_id)
    if lock is None:  # no await between .get() and the set -> race-free on the single-threaded loop
        lock = asyncio.Lock()
        locks[target_id] = lock
    return lock


async def _invalidate_target(state: Any, target_id: str) -> None:
    """Drop only ``target_id``'s cached verify outcome (a new/removed credential changes it).

    Taken **under that target's verify lock**, which is the whole point of the await. A bind runs
    under the global bind lock while a ``?verify=1`` for the same target can be in flight under the
    verify lock, and that probe writes its result when it returns: popping the entry without the
    lock let a pre-bind outcome land *after* the invalidate and pin the stale answer for the full
    60s TTL — the panel then reported "not bound" for a minute after a successful bind. Waiting for
    the lock means the in-flight probe has written before we drop what it wrote.
    """
    async with _verify_lock(state, target_id):
        cache: dict[str, tuple[float, AssistantVerified]] | None = getattr(state, "assistant_verified_by_id", None)
        if cache is not None:
            cache.pop(target_id, None)


async def _verify(request: Request, info: AssistantInfo, target_id: str) -> AssistantVerified:
    """Run ``info``'s verify tool for ``target_id`` (per-id 60s TTL cache + per-id single-flight lock).

    Caller guarantees ``info.verify`` is set. Isolation: the cache slot and lock are keyed by
    ``target_id``, so verifying target A never populates or waits on target B's cache.
    """
    state = request.app.state
    spec = info.verify
    assert spec is not None  # callers only reach here when a verify spec is declared
    if (fresh := _fresh_target(state, target_id)) is not None:
        return fresh
    async with _verify_lock(state, target_id):
        if (fresh := _fresh_target(state, target_id)) is not None:  # a racing caller refreshed our slot
            return fresh
        # First ?verify=1 for a lazily-deferred target: spawn its bridge before probing, so the verify
        # tool is attached. Awaited under the per-id lock (concurrent verifies single-flight the spawn);
        # a spawn failure just leaves the tool absent -> _run_verify reports a clean ok=False data state.
        bridge: MCPBridge | None = getattr(state, "mcp_bridge", None)
        routing: TargetRouting | None = getattr(state, "target_routing", None)
        if bridge is not None and routing is not None:
            await bridge.ensure_target(routing, target_id)
        toolset: MCPToolset | None = getattr(state, "toolset", None)
        verified = await _run_verify(toolset, spec)
        cache: dict[str, tuple[float, AssistantVerified]] | None = getattr(state, "assistant_verified_by_id", None)
        if cache is None:
            cache = {}
            state.assistant_verified_by_id = cache
        cache[target_id] = (verified.checked_at, verified)
        return verified


def _registry(request: Request) -> AssistantRegistry:
    """The app's assistant registry (all targets), or an empty one outside a project."""
    return getattr(request.app.state, "registry", None) or AssistantRegistry()


def _lazy_state(request: Request) -> tuple[MCPBridge | None, TargetRouting | None]:
    """The lazy bridge + routing table (for honest ``can_verify`` on not-yet-spawned targets)."""
    state = request.app.state
    return getattr(state, "mcp_bridge", None), getattr(state, "target_routing", None)


async def _ensure_target_bridge(request: Request, target_id: str) -> None:
    """First-use trigger: spawn ``target_id``'s lazily-deferred servers (no-op without a bridge)."""
    bridge, routing = _lazy_state(request)
    if bridge is not None and routing is not None:
        await bridge.ensure_target(routing, target_id)


def _target(request: Request, target_id: str) -> tuple[AssistantInfo, str]:
    """Resolve a target by its collision-safe registry id, or 404 for an unknown id."""
    registry = _registry(request)
    info = registry.by_id(target_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Unknown assistant target: {target_id}")
    return info, target_id


def _compat_id(request: Request, info: AssistantInfo) -> str:
    """The registry id the /api/assistant compat route keys its verify cache + response ``id`` by.

    Normally the primary's registry id (``registry.ids[0]``), so the compat route and
    ``/api/assistants/{primary}`` share one verify slot (single-flight against a rate-limited instance).
    When ``app.state.assistant`` was set without a registry (state-poking tests), fall back to the id a
    one-target registry would have given the primary, so the key and the echoed ``id`` are stable either
    way.
    """
    registry = _registry(request)
    if registry.ids:
        return registry.ids[0]
    return (_slugify(info.name) if info.name else "") or "target-0"


@router.get("/api/assistants")
async def assistants(request: Request, url: str | None = None) -> JSONResponse:
    """The sanitized registry: ``{"targets": [...]}``; ``?url=<tabUrl>`` adds ``selected`` / ``matches``.

    An empty registry returns ``{"targets": []}`` with 200 (never 404) — the extension reads a generic
    (no-target) server as an empty list. ``?url=`` reports which target(s) the tab falls under:
    ``matches`` is every candidate (most-specific first, :func:`stabbur.targets.select`) and ``selected`` is
    the unique unambiguous pick (:func:`stabbur.targets.selected`) — set even when a broad catch-all also
    matches (the more-specific target wins), ``null`` only on a real top-rank tie or no match.
    """
    state = request.app.state
    registry = _registry(request)
    toolset: MCPToolset | None = getattr(state, "toolset", None)
    bridge, routing = _lazy_state(request)
    targets = [
        _sanitize(info, toolset, target_id=target_id, bridge=bridge, routing=routing)
        for target_id, info in zip(registry.ids, registry.targets, strict=True)
    ]
    if url is None:
        payload = AssistantListResponse(targets=targets).model_dump(exclude={"selected", "matches"})
        return JSONResponse(payload)
    payload = AssistantListResponse(
        targets=targets, selected=selected(url, registry), matches=select(url, registry)
    ).model_dump()
    return JSONResponse(payload)


@router.get("/api/assistants/{target_id}")
async def assistant_target(request: Request, target_id: str, verify: bool = False) -> AssistantResponse:
    """Echo one target by id (404 if unknown); ``?verify=1`` probes just that target (cached 60s per id)."""
    info, resolved_id = _target(request, target_id)
    toolset: MCPToolset | None = getattr(request.app.state, "toolset", None)
    bridge, routing = _lazy_state(request)
    response = _sanitize(info, toolset, target_id=resolved_id, bridge=bridge, routing=routing)
    if verify and info.verify is not None:
        response.verified = await _verify(request, info, resolved_id)
    return response


@router.get("/api/assistant")
async def assistant(request: Request, verify: bool = False) -> AssistantResponse:
    """Compat: the project's primary target (404 if none); ``?verify=1`` probes it.

    Reads ``app.state.assistant`` (the registry's primary) and keys into the *shared* per-id verify cache
    by the primary's id, so old clients and ``/api/assistants/{primary}`` never double-probe. ``can_verify``
    reports whether a live probe is possible; ``?verify=1`` runs it (cached 60s) and reports the outcome.
    """
    info: AssistantInfo | None = getattr(request.app.state, "assistant", None)
    if info is None:
        raise HTTPException(status_code=404, detail="No assistant metadata")
    toolset: MCPToolset | None = getattr(request.app.state, "toolset", None)
    bridge, routing = _lazy_state(request)
    compat_id = _compat_id(request, info)
    response = _sanitize(info, toolset, target_id=compat_id, bridge=bridge, routing=routing)
    if verify and info.verify is not None:
        response.verified = await _verify(request, info, compat_id)
    return response


@router.post("/api/assistants/{target_id}/verify")
async def assistant_target_verify(request: Request, target_id: str) -> AssistantResponse:
    """Probe one target and echo it with the outcome — the POST form of ``GET …?verify=1``.

    Same work, honest method. Verifying *runs* something: it spawns a lazily-deferred target's MCP
    servers and calls the project's verify tool against the instance. That is a side effect, and a
    side effect behind a GET rides the cross-site guard's read exemption — a drive-by ``<img src>``
    on any page the user has open could fire it. The GET form still works (it is what shipped
    clients call, and the guard now treats a ``?verify=`` GET as mutating), but this is the shape
    that says what the call does.

    404 for an unknown target id. A target with no verify recipe is echoed with ``verified`` unset,
    exactly as the GET does — not an error, just nothing to run.
    """
    info, resolved_id = _target(request, target_id)
    toolset: MCPToolset | None = getattr(request.app.state, "toolset", None)
    bridge, routing = _lazy_state(request)
    response = _sanitize(info, toolset, target_id=resolved_id, bridge=bridge, routing=routing)
    if info.verify is not None:
        response.verified = await _verify(request, info, resolved_id)
    return response


@router.post("/api/assistant/verify")
async def assistant_verify(request: Request) -> AssistantResponse:
    """Compat: probe the primary target and echo it (the POST form of ``GET /api/assistant?verify=1``)."""
    info: AssistantInfo | None = getattr(request.app.state, "assistant", None)
    if info is None:
        raise HTTPException(status_code=404, detail="No assistant metadata")
    toolset: MCPToolset | None = getattr(request.app.state, "toolset", None)
    bridge, routing = _lazy_state(request)
    compat_id = _compat_id(request, info)
    response = _sanitize(info, toolset, target_id=compat_id, bridge=bridge, routing=routing)
    if info.verify is not None:
        response.verified = await _verify(request, info, compat_id)
    return response


def _template_argv(argv: list[str], info: AssistantInfo) -> list[str]:
    """Substitute ``{base_url}`` / ``{name}`` in a bind mode's argv from the AssistantInfo fields.

    A placeholder whose field is unset is a 400 (the bind needs it) rather than a literal ``{base_url}``
    reaching the subprocess. Only these two tokens are allowed (enforced at parse time in the model);
    the allowed set + regex are imported from :mod:`stabbur.project` so there's one source of truth.
    """
    values: dict[str, str | None] = {token: getattr(info, token) for token in ALLOWED_COMMAND_PLACEHOLDERS}

    def _sub(arg: str) -> str:
        for token in COMMAND_PLACEHOLDER_RE.findall(arg):
            if values.get(token) is None:
                raise HTTPException(status_code=400, detail=f"bind requires assistant.{token}")
        # One pass over the whole arg: a value that itself contains a literal "{name}" is never
        # re-substituted (a per-token sequential str.replace would double-substitute it).
        return COMMAND_PLACEHOLDER_RE.sub(lambda m: values[m.group(1)] or "", arg)

    return [_sub(arg) for arg in argv]


async def _run_mode(
    request: Request,
    argv: list[str],
    *,
    secret_env: str | None,
    secret: str,
    extra_secret_env: str | None = None,
    extra_secret: str | None = None,
    timeout: float,
    invalidate: Callable[[], Awaitable[None]] | None = None,
) -> BindResult:
    """Run a bind/unbind mode's argv once, serialized on the one global ``app.state.assistant_bind_lock``.

    One lock guards *all* bind/unbind runs (regardless of target) so two credential installs never race.
    The secret (bind only) is passed via ``secret_env`` in the child env, never on the argv, and every
    literal occurrence of the secret string is redacted from the captured output (encoded copies are
    not covered). A mode may declare a second env var (``extra_secret_env``); when the caller also
    supplies ``extra_secret`` it is exported the same way and redacted too. A timeout kills the whole
    process group and reports ok=False. On success ``invalidate`` (if given) drops the right verify-cache
    entry so the panel re-probes the freshly-bound session.
    """
    state = request.app.state
    lock: asyncio.Lock | None = getattr(state, "assistant_bind_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        state.assistant_bind_lock = lock
    env = {**os.environ}
    if secret_env is not None:
        env[secret_env] = secret
    if extra_secret_env is not None and extra_secret:
        env[extra_secret_env] = extra_secret
    async with lock:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=Path.cwd(),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,  # a prompting command fails fast, never hangs to timeout
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # own process group so a timeout can killpg any grandchildren
            )
        except OSError:
            # argv[0] missing / not executable — a data state, not a 500. 127 mirrors a shell's
            # "command not found" so the client can distinguish it from a real non-zero exit.
            return BindResult(ok=False, exit_code=127, stderr=f"command not found: {argv[0]}")
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            exit_code = proc.returncode
            secrets = (secret, extra_secret)
            stdout, stderr = _capture(out, secrets), _capture(err, secrets)
            ok = exit_code == 0
        except TimeoutError:
            _killpg(proc)  # whole group: a plain proc.kill() would orphan grandchildren holding the secret
            await proc.wait()
            exit_code, stdout, ok = proc.returncode, "", False
            stderr = f"bind command timed out after {timeout}s"  # our own text: nothing to redact
        if ok and invalidate is not None:
            await invalidate()  # a new/removed credential changes what verify would report
        return BindResult(ok=ok, exit_code=exit_code, stdout=stdout, stderr=stderr)


def _bind_mode(info: AssistantInfo | None, mode: str) -> tuple[AssistantInfo, BindMode]:
    """Resolve a target's named bind mode, or raise the right HTTP error (404 no recipe / 400 bad mode)."""
    if info is None or info.bind is None:
        raise HTTPException(status_code=404, detail="No assistant bind recipe")
    spec = info.bind.modes.get(mode)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"Unknown bind mode: {mode}")
    return info, spec


async def _do_bind(
    request: Request, info: AssistantInfo, body: BindRequest, invalidate: Callable[[], Awaitable[None]]
) -> BindResult:
    """Validate + run a bind mode: check secret sizes, template the argv, run it with the secret in env."""
    _, spec = _bind_mode(info, body.mode)
    if not body.secret:
        raise HTTPException(status_code=400, detail="secret is required")
    if len(body.secret) > _MAX_SECRET:
        raise HTTPException(status_code=400, detail=f"secret exceeds {_MAX_SECRET} characters")
    if body.extra_secret is not None and len(body.extra_secret) > _MAX_SECRET:
        raise HTTPException(status_code=400, detail=f"extra_secret exceeds {_MAX_SECRET} characters")
    argv = _template_argv(spec.command, info)
    return await _run_mode(
        request,
        argv,
        secret_env=spec.secret_env,
        secret=body.secret,
        extra_secret_env=spec.extra_secret_env,
        extra_secret=body.extra_secret,
        timeout=spec.timeout,
        invalidate=invalidate,
    )


async def _do_unbind(
    request: Request, info: AssistantInfo, mode: str, invalidate: Callable[[], Awaitable[None]]
) -> BindResult:
    """Validate + run a mode's ``unbind_command`` (400 if the mode declares none)."""
    _, spec = _bind_mode(info, mode)
    if spec.unbind_command is None:
        raise HTTPException(status_code=400, detail=f"bind mode {mode} has no unbind_command")
    argv = _template_argv(spec.unbind_command, info)
    return await _run_mode(request, argv, secret_env=None, secret="", timeout=spec.timeout, invalidate=invalidate)


@router.post("/api/assistants/{target_id}/bind")
async def assistant_target_bind(request: Request, target_id: str, body: BindRequest) -> BindResult:
    """Install a bound credential for one target by running its named mode's argv with the secret in env.

    404 for an unknown target id or a target with no ``bind`` recipe; 400 for an unknown mode or an
    empty/oversized secret. On success only that target's verify cache is invalidated.
    """
    info, resolved_id = _target(request, target_id)
    await _ensure_target_bridge(request, resolved_id)  # first-use trigger: warm the lazy bridge
    return await _do_bind(request, info, body, lambda: _invalidate_target(request.app.state, resolved_id))


@router.post("/api/assistants/{target_id}/unbind")
async def assistant_target_unbind(request: Request, target_id: str, body: UnbindRequest) -> BindResult:
    """Reverse one target's bound credential by running its mode's ``unbind_command``.

    404 for an unknown target id or no ``bind`` recipe; 400 for an unknown mode or one with no
    ``unbind_command``. On success only that target's verify cache is invalidated.
    """
    info, resolved_id = _target(request, target_id)
    await _ensure_target_bridge(request, resolved_id)  # first-use trigger: warm the lazy bridge
    return await _do_unbind(request, info, body.mode, lambda: _invalidate_target(request.app.state, resolved_id))


@router.post("/api/assistant/bind")
async def assistant_bind(request: Request, body: BindRequest) -> BindResult:
    """Compat: install a bound credential on the primary target (reads ``app.state.assistant``).

    404 if no ``[assistant.bind]`` is declared; 400 for an unknown mode or an empty/oversized secret.
    The secret is handed to the child via the env only (never the argv) and redacted from the output.
    """
    info: AssistantInfo | None = getattr(request.app.state, "assistant", None)
    _bind_mode(info, body.mode)  # 404/400 before validation, as before
    assert info is not None  # _bind_mode raised otherwise
    compat_id = _compat_id(request, info)
    return await _do_bind(request, info, body, lambda: _invalidate_target(request.app.state, compat_id))


@router.post("/api/assistant/unbind")
async def assistant_unbind(request: Request, body: UnbindRequest) -> BindResult:
    """Compat: reverse the primary target's bound credential by running the named mode's ``unbind_command``.

    404 if no ``[assistant.bind]``; 400 for an unknown mode or a mode with no ``unbind_command``.
    """
    info: AssistantInfo | None = getattr(request.app.state, "assistant", None)
    _bind_mode(info, body.mode)  # 404/400 before running, as before
    assert info is not None  # _bind_mode raised otherwise
    compat_id = _compat_id(request, info)
    return await _do_unbind(request, info, body.mode, lambda: _invalidate_target(request.app.state, compat_id))
