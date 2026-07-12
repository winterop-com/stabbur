"""Assistant metadata API: echo the project's ``[assistant]`` block for UI clients.

heim stays domain-generic — it never interprets these fields. ``GET /api/assistant`` returns the
``[assistant]`` metadata (target ``name`` / ``base_url`` / ``auth`` / …) verbatim so a UI client
(the Chrome side panel) can show what instance the assistant targets. With ``?verify=1`` it runs
the project-declared verify tool (an MCP tool) once and caches the outcome for 60s, so the panel
can show a live connection state without heim knowing what "connected" means for the domain.
"""

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict

from heim.project import (
    ALLOWED_COMMAND_PLACEHOLDERS,
    COMMAND_PLACEHOLDER_RE,
    AssistantInfo,
    AssistantVerify,
    BindMode,
)
from heim.routers.serving._base import router
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
    """The project's ``[assistant]`` metadata, echoed for UI clients (heim never interprets it).

    Mirrors :class:`heim.project.AssistantInfo`'s public fields minus ``verify`` (an execution
    detail, never surfaced); ``extra="allow"`` lets a project's unknown keys ride along. ``probe`` is
    echoed verbatim (it's *for* the client to run). ``bind`` is echoed sanitized: the browser-side mint
    recipe plus only the mode *names* — a mode's argv / secret_env are server-side execution details.
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    base_url: str | None = None
    auth: str | None = None
    readonly: bool | None = None
    source: str | None = None
    can_verify: bool = False
    verified: AssistantVerified | None = None
    probe: dict[str, Any] | None = None
    can_bind: bool = False
    bind: dict[str, Any] | None = None


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


def _fresh(state: Any) -> AssistantVerified | None:
    """The cached verify outcome, if one exists and is within the TTL."""
    cached: tuple[float, AssistantVerified] | None = getattr(state, "assistant_verified", None)
    if cached is not None and time.time() - cached[0] < _VERIFY_TTL:
        return cached[1]
    return None


async def _verify(request: Request, spec: AssistantVerify) -> AssistantVerified:
    """Run the verify tool once (60s TTL cache on ``app.state.assistant_verified``).

    Any failure — unknown tool, timeout, no toolset — is a data state, not an API error: it
    returns ``ok=False`` with the message so the caller still gets HTTP 200. Concurrent callers
    that race an empty/expired cache share one probe (single-flight lock) so panel polling can't
    double-hit a rate-limited target; the lock is created lazily, which is race-free on the
    single-threaded event loop (no await between check and set).
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
        try:
            if toolset is None:
                raise RuntimeError("no MCP tools available to verify")
            result = await toolset.call_structured(spec.tool, spec.args, timeout=spec.timeout)
            verified = AssistantVerified(ok=True, data=_coerce(result), checked_at=time.time())
        except KeyError:  # call_structured raises KeyError(name); its str() is just "'name'"
            error = f"verify tool not attached: {spec.tool}"
            verified = AssistantVerified(ok=False, error=error, checked_at=time.time())
        except Exception as exc:  # noqa: BLE001 - a verify failure is reported as state, never raised
            verified = AssistantVerified(ok=False, error=str(exc), checked_at=time.time())
        state.assistant_verified = (verified.checked_at, verified)
        return verified


@router.get("/api/assistant")
async def assistant(request: Request, verify: bool = False) -> AssistantResponse:
    """Return the project's ``[assistant]`` metadata (404 if none); ``?verify=1`` probes the target.

    Read-only echo — heim never interprets these fields. ``can_verify`` reports whether a live
    probe is possible (a verify spec is declared and its tool is attached); ``?verify=1`` actually
    runs it (cached 60s) and reports the outcome in ``verified``.
    """
    info: AssistantInfo | None = getattr(request.app.state, "assistant", None)
    if info is None:
        raise HTTPException(status_code=404, detail="No assistant metadata")
    # Echo everything except verify + bind (execution details / handled separately); probe rides
    # through as-is (it's for the client to run). Built as a dict (not **kwargs) so a project extra key
    # named after a response field (can_verify, verified, can_bind, bind) is overridden, not a TypeError.
    base = info.model_dump(exclude={"verify", "bind"}, exclude_none=True)
    toolset: MCPToolset | None = getattr(request.app.state, "toolset", None)
    spec = info.verify
    can_verify = spec is not None and toolset is not None and _tool_available(toolset, spec.tool)
    # Sanitize the bind echo: the browser-side mint recipe plus only the mode names (a mode's argv /
    # secret_env are server-side execution details, mirroring verify's exclusion). can_bind means a
    # bind block is declared with at least one runnable mode.
    bind_echo: dict[str, Any] | None = None
    can_bind = False
    if info.bind is not None:
        bind_echo = info.bind.model_dump(exclude={"modes"}, exclude_none=True)
        bind_echo["modes"] = sorted(info.bind.modes)
        # A mode's user-facing unbind_note is guidance for the client (not an execution detail like
        # command / secret_env), so surface it keyed by mode. Plucked explicitly so a mode's
        # extra="allow" fields can never ride into the echo — only the note, keyed by mode name.
        unbind_notes = {name: mode.unbind_note for name, mode in info.bind.modes.items() if mode.unbind_note}
        if unbind_notes:
            bind_echo["unbind_notes"] = unbind_notes
        can_bind = bool(info.bind.modes)
    response = AssistantResponse.model_validate(
        {**base, "can_verify": can_verify, "verified": None, "can_bind": can_bind, "bind": bind_echo}
    )
    if verify and spec is not None:
        response.verified = await _verify(request, spec)
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
) -> BindResult:
    """Run a bind/unbind mode's argv once, serialized on ``app.state.assistant_bind_lock``.

    The secret (bind only) is passed via ``secret_env`` in the child env, never on the argv, and every
    literal occurrence of the secret string is redacted from the captured output (encoded copies are
    not covered). A mode may declare a second env var (``extra_secret_env``); when the caller also
    supplies ``extra_secret`` it is exported the same way and redacted too. A timeout kills the whole
    process group and reports ok=False. On success the verify cache is invalidated so the panel
    re-probes the freshly-bound session.
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
        if ok:
            state.assistant_verified = None  # a new/removed credential changes what verify would report
        return BindResult(ok=ok, exit_code=exit_code, stdout=stdout, stderr=stderr)


def _bind_mode(request: Request, mode: str) -> tuple[AssistantInfo, BindMode]:
    """Resolve the assistant + a named bind mode, or raise the right HTTP error (404/400)."""
    info: AssistantInfo | None = getattr(request.app.state, "assistant", None)
    if info is None or info.bind is None:
        raise HTTPException(status_code=404, detail="No assistant bind recipe")
    spec = info.bind.modes.get(mode)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"Unknown bind mode: {mode}")
    return info, spec


@router.post("/api/assistant/bind")
async def assistant_bind(request: Request, body: BindRequest) -> BindResult:
    """Install a bound credential by running the named mode's argv with the secret in ``secret_env``.

    404 if no ``[assistant.bind]`` is declared; 400 for an unknown mode or an empty/oversized secret.
    The secret is handed to the child via the env only (never the argv) and redacted from the output.
    """
    info, spec = _bind_mode(request, body.mode)
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
    )


@router.post("/api/assistant/unbind")
async def assistant_unbind(request: Request, body: UnbindRequest) -> BindResult:
    """Reverse a bound credential by running the named mode's ``unbind_command``.

    404 if no ``[assistant.bind]``; 400 for an unknown mode or a mode with no ``unbind_command``.
    """
    info, spec = _bind_mode(request, body.mode)
    if spec.unbind_command is None:
        raise HTTPException(status_code=400, detail=f"bind mode {body.mode} has no unbind_command")
    argv = _template_argv(spec.unbind_command, info)
    return await _run_mode(request, argv, secret_env=None, secret="", timeout=spec.timeout)
