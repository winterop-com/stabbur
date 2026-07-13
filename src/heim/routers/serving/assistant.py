"""Assistant metadata API: echo the project's assistant target(s) for UI clients.

heim stays domain-generic — it never interprets these fields. A project can declare **one** target
(``[assistant]``) or **many** (``[[assistants]]``, e.g. one DHIS2 instance per path on an origin).

- ``GET /api/assistants`` returns the sanitized registry (every target); ``?url=<tabUrl>`` adds which
  target a browser tab falls under (``selected`` / ``matches`` via :func:`heim.targets.select`).
- ``GET /api/assistants/{id}`` echoes one target; ``?verify=1`` runs its verify tool (cached 60s per id).
- ``POST /api/assistants/{id}/bind|unbind`` runs that target's bind recipe.
- ``GET /api/assistant`` (+ ``?verify=1``) and ``POST /api/assistant/bind|unbind`` are the **compat**
  single-target routes for old clients — they read ``app.state.assistant`` (the registry's primary) and
  keep their own single-slot verify cache (``app.state.assistant_verified``), byte-identical to before.

Sanitized echo: a target's ``verify`` spec and a bind mode's ``command`` / ``secret_env`` are server-side
execution details, never surfaced; ``probe`` rides through verbatim (it's *for* the client to run) and the
bind echo carries only the mint recipe, mode *names*, and per-mode ``unbind_note``.
"""

import asyncio
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from heim.project import (
    ALLOWED_COMMAND_PLACEHOLDERS,
    COMMAND_PLACEHOLDER_RE,
    AssistantInfo,
    AssistantVerify,
    BindMode,
)
from heim.routers.serving._base import router
from heim.targets import AssistantRegistry, select
from heim.tools import MCPToolset

_VERIFY_TTL = 60.0  # seconds a verify outcome is cached, so ?verify=1 polling doesn't re-probe each call
_MAX_SECRET = 16384  # cap on a bind secret so a caller can't shove an unbounded blob into the process env
_MAX_OUTPUT = 16384  # cap on each of a mode's captured stdout/stderr (bounds RAM, redaction, response)


def _capture(raw: bytes) -> str:
    """Decode captured child output, capping it so a chatty command can't blow up RAM / the response.

    Keeps the first ``_MAX_OUTPUT`` bytes (which also bounds the redaction scan and the JSON response
    size), appending a truncation marker so the caller can tell the output was cut.
    """
    text = raw[:_MAX_OUTPUT].decode(errors="replace")
    if len(raw) > _MAX_OUTPUT:
        text += "... [truncated]"
    return text


def _killpg(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the child's whole process group (it was spawned with ``start_new_session``).

    Mirrors :mod:`heim.runtime.supervisor`: a plain ``proc.kill()`` signals only the direct child, so
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
    """One target's metadata, echoed for UI clients (heim never interprets it).

    Mirrors :class:`heim.project.AssistantInfo`'s public fields minus ``verify`` (an execution
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
    single unambiguous pick — ``null`` on a tie or no match, so the client shows a picker or nothing.
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
) -> AssistantResponse:
    """Build one target's sanitized echo (verify internals + bind argv/secret_env excluded).

    Shared by the compat single-target route and the multi-target list/detail routes. ``can_verify``
    reports whether a live probe is possible (a verify spec is declared and its tool is attached).
    ``target_id`` (when given) overrides the info's own computed ``id`` with the registry's
    collision-safe id. Built as a dict (not ``**kwargs``) so a project extra key named after a response
    field (``can_verify`` / ``verified`` / ``can_bind`` / ``bind``) is overridden, not a ``TypeError``.
    """
    base = info.model_dump(exclude={"verify", "bind"}, exclude_none=True)
    spec = info.verify
    can_verify = spec is not None and toolset is not None and _tool_available(toolset, spec.tool)
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
    still returns HTTP 200 — heim doesn't know what "connected" means for the domain, only whether the
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


# --- Compat single-target verify cache (app.state.assistant_verified, one slot) -----------------------
# The /api/assistant* routes keep their own single-slot cache + single lock, byte-identical to before
# the registry landed. It is deliberately *separate* from the per-id cache below: old clients hitting
# /api/assistant and new clients hitting /api/assistants/{id} don't share a slot, but each surface is
# internally consistent (and the test_api.py contract pins the single slot as a tuple / None).


def _fresh(state: Any) -> AssistantVerified | None:
    """The compat single-slot cached verify outcome, if one exists and is within the TTL."""
    cached: tuple[float, AssistantVerified] | None = getattr(state, "assistant_verified", None)
    if cached is not None and time.time() - cached[0] < _VERIFY_TTL:
        return cached[1]
    return None


async def _verify_compat(request: Request, spec: AssistantVerify) -> AssistantVerified:
    """Compat verify for /api/assistant: 60s TTL cache on ``app.state.assistant_verified`` (one slot).

    Concurrent callers that race an empty/expired cache share one probe (single-flight lock) so panel
    polling can't double-hit a rate-limited target; the lock is created lazily, race-free on the
    single-threaded event loop (no await between the check and the set).
    """
    state = request.app.state
    if (fresh := _fresh(state)) is not None:
        return fresh
    lock: asyncio.Lock | None = getattr(state, "assistant_verify_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        state.assistant_verify_lock = lock
    async with lock:
        if (fresh := _fresh(state)) is not None:  # a concurrent caller refreshed while we waited
            return fresh
        toolset: MCPToolset | None = getattr(state, "toolset", None)
        verified = await _run_verify(toolset, spec)
        state.assistant_verified = (verified.checked_at, verified)
        return verified


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


def _invalidate_target(state: Any, target_id: str) -> None:
    """Drop only ``target_id``'s cached verify outcome (a new/removed credential changes it)."""
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
    locks: dict[str, asyncio.Lock] | None = getattr(state, "assistant_verify_locks", None)
    if locks is None:
        locks = {}
        state.assistant_verify_locks = locks
    lock = locks.get(target_id)
    if lock is None:  # no await between .get() and the set -> race-free on the single-threaded loop
        lock = asyncio.Lock()
        locks[target_id] = lock
    async with lock:
        if (fresh := _fresh_target(state, target_id)) is not None:  # a racing caller refreshed our slot
            return fresh
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


def _target(request: Request, target_id: str) -> tuple[AssistantInfo, str]:
    """Resolve a target by its collision-safe registry id, or 404 for an unknown id."""
    registry = _registry(request)
    info = registry.by_id(target_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Unknown assistant target: {target_id}")
    return info, target_id


@router.get("/api/assistants")
async def assistants(request: Request, url: str | None = None) -> JSONResponse:
    """The sanitized registry: ``{"targets": [...]}``; ``?url=<tabUrl>`` adds ``selected`` / ``matches``.

    An empty registry returns ``{"targets": []}`` with 200 (never 404) — the extension reads a generic
    (no-target) server as an empty list. ``?url=`` reports which target(s) the tab falls under via
    :func:`heim.targets.select`: ``selected`` is set only on a single unambiguous match, ``null`` on a
    tie (both ids still in ``matches``) or no match.
    """
    state = request.app.state
    registry = _registry(request)
    toolset: MCPToolset | None = getattr(state, "toolset", None)
    targets = [
        _sanitize(info, toolset, target_id=target_id)
        for target_id, info in zip(registry.ids, registry.targets, strict=True)
    ]
    if url is None:
        payload = AssistantListResponse(targets=targets).model_dump(exclude={"selected", "matches"})
        return JSONResponse(payload)
    matches = select(url, registry)
    selected = matches[0] if len(matches) == 1 else None
    payload = AssistantListResponse(targets=targets, selected=selected, matches=matches).model_dump()
    return JSONResponse(payload)


@router.get("/api/assistants/{target_id}")
async def assistant_target(request: Request, target_id: str, verify: bool = False) -> AssistantResponse:
    """Echo one target by id (404 if unknown); ``?verify=1`` probes just that target (cached 60s per id)."""
    info, resolved_id = _target(request, target_id)
    toolset: MCPToolset | None = getattr(request.app.state, "toolset", None)
    response = _sanitize(info, toolset, target_id=resolved_id)
    if verify and info.verify is not None:
        response.verified = await _verify(request, info, resolved_id)
    return response


@router.get("/api/assistant")
async def assistant(request: Request, verify: bool = False) -> AssistantResponse:
    """Compat: the project's primary target (404 if none); ``?verify=1`` probes it.

    Reads ``app.state.assistant`` (the registry's primary) and the single-slot verify cache, so old
    clients see byte-identical behavior. ``can_verify`` reports whether a live probe is possible;
    ``?verify=1`` runs it (cached 60s) and reports the outcome in ``verified``.
    """
    info: AssistantInfo | None = getattr(request.app.state, "assistant", None)
    if info is None:
        raise HTTPException(status_code=404, detail="No assistant metadata")
    toolset: MCPToolset | None = getattr(request.app.state, "toolset", None)
    response = _sanitize(info, toolset)
    if verify and info.verify is not None:
        response.verified = await _verify_compat(request, info.verify)
    return response


def _template_argv(argv: list[str], info: AssistantInfo) -> list[str]:
    """Substitute ``{base_url}`` / ``{name}`` in a bind mode's argv from the AssistantInfo fields.

    A placeholder whose field is unset is a 400 (the bind needs it) rather than a literal ``{base_url}``
    reaching the subprocess. Only these two tokens are allowed (enforced at parse time in the model);
    the allowed set + regex are imported from :mod:`heim.project` so there's one source of truth.
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
    invalidate: Callable[[], None] | None = None,
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
            stdout, stderr = _capture(out), _capture(err)
            ok = exit_code == 0
        except TimeoutError:
            _killpg(proc)  # whole group: a plain proc.kill() would orphan grandchildren holding the secret
            await proc.wait()
            exit_code, stdout, ok = proc.returncode, "", False
            stderr = f"bind command timed out after {timeout}s"
        for value in (secret, extra_secret):
            if value:
                stdout, stderr = stdout.replace(value, "***"), stderr.replace(value, "***")
        if ok and invalidate is not None:
            invalidate()  # a new/removed credential changes what verify would report
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
    request: Request, info: AssistantInfo, body: BindRequest, invalidate: Callable[[], None]
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


async def _do_unbind(request: Request, info: AssistantInfo, mode: str, invalidate: Callable[[], None]) -> BindResult:
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
    return await _do_bind(request, info, body, lambda: _invalidate_target(request.app.state, resolved_id))


@router.post("/api/assistants/{target_id}/unbind")
async def assistant_target_unbind(request: Request, target_id: str, body: UnbindRequest) -> BindResult:
    """Reverse one target's bound credential by running its mode's ``unbind_command``.

    404 for an unknown target id or no ``bind`` recipe; 400 for an unknown mode or one with no
    ``unbind_command``. On success only that target's verify cache is invalidated.
    """
    info, resolved_id = _target(request, target_id)
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
    return await _do_bind(request, info, body, lambda: setattr(request.app.state, "assistant_verified", None))


@router.post("/api/assistant/unbind")
async def assistant_unbind(request: Request, body: UnbindRequest) -> BindResult:
    """Compat: reverse the primary target's bound credential by running the named mode's ``unbind_command``.

    404 if no ``[assistant.bind]``; 400 for an unknown mode or a mode with no ``unbind_command``.
    """
    info: AssistantInfo | None = getattr(request.app.state, "assistant", None)
    _bind_mode(info, body.mode)  # 404/400 before running, as before
    assert info is not None  # _bind_mode raised otherwise
    return await _do_unbind(request, info, body.mode, lambda: setattr(request.app.state, "assistant_verified", None))
