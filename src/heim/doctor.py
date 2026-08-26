"""System health checks for ``heim doctor``.

Each check is a pure function returning a :class:`Check`, so the whole report is
testable without a terminal. The CLI (:func:`heim.cli.doctor`) renders them.

Checks cover the things that make ``heim run/serve`` actually work: the backend the
models run on (local runtimes or a remote ``/v1``), the runtime binaries heim spawns
(llama.cpp / MLX), the library location, what's in it, and the current project manifest.
"""

import socket
from enum import StrEnum
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from heim import host, mcpservers, runtime, server
from heim import library as library_ops
from heim import project as project_ops
from heim.config import Settings, get_settings

# Timeout for the upstream health probe. Deliberately NOT UpstreamManager._LISTING_TIMEOUT (15s):
# that budget exists so a llama-server busy generating is never mis-reported as an outage on the
# serving path, and it's paid at most once per model swap. This probe is different — `heim doctor`
# runs it interactively and the UI polls it via GET /api/doctor, so a dead host must not stall a
# terminal (or an event-loop worker) for fifteen seconds. Five still clears the 1-3s a busy
# llama-server takes to answer /v1/models, so a slow-but-alive remote reads as ok while a black
# hole gives up fast.
_UPSTREAM_TIMEOUT = 5.0


class CheckStatus(StrEnum):
    """Outcome of a single health check."""

    ok = "ok"
    warn = "warn"
    fail = "fail"


class Check(BaseModel):
    """One health-check result."""

    name: str
    status: CheckStatus
    detail: str
    hint: str | None = None
    # The ``name`` of the check this one nests under (``None`` = a top-level row). The hierarchy
    # travels in the payload rather than being re-derived from name prefixes by each consumer:
    # a UI parsing names back into a tree breaks the next time a check is renamed. Optional, so
    # every other check is unaffected and a consumer that ignores it still renders a flat list.
    group: str | None = None


# Parent row for the per-server MCP checks (emitted by check_project, children by the API layer's
# _mcp_checks). Named once here so the two sides can't drift apart on the string.
MCP_GROUP = "Tools (MCP)"


class DoctorReport(BaseModel):
    """The full set of checks plus a rolled-up worst status."""

    checks: list[Check]

    @property
    def status(self) -> CheckStatus:
        """The worst status across all checks (fail > warn > ok)."""
        if any(c.status is CheckStatus.fail for c in self.checks):
            return CheckStatus.fail
        if any(c.status is CheckStatus.warn for c in self.checks):
            return CheckStatus.warn
        return CheckStatus.ok


def check_platform() -> list[Check]:
    """Report the OS/arch so the rest of the report (esp. N/A rows) reads in context."""
    return [Check(name="Platform", status=CheckStatus.ok, detail=host.os_label())]


def _runtime_check(name: str, binary: str, *, required: bool, relevant: bool = True) -> Check:
    """Check that a runtime binary is on PATH.

    Args:
        name: Human label for the check.
        binary: Executable to look for.
        required: If missing, ``fail`` (a needed runtime) vs ``warn`` (optional).
        relevant: If false (e.g. MLX off Apple Silicon), report ``ok`` as N/A.
    """
    path = runtime.resolve_binary(binary)
    if path is not None:
        return Check(name=name, status=CheckStatus.ok, detail=path)
    if not relevant:
        return Check(name=name, status=CheckStatus.ok, detail="not applicable on this platform")
    return Check(
        name=name,
        status=CheckStatus.fail if required else CheckStatus.warn,
        detail=f"{binary!r} not found (checked heim's environment and PATH)",
        hint=runtime._INSTALL_HINTS.get(binary),
    )


def check_runtimes() -> list[Check]:
    """Check the model-runtime binaries heim spawns."""
    mlx_relevant = host.is_apple_silicon()
    return [
        # GGUF is the cross-platform backbone; without llama-server nothing GGUF runs.
        _runtime_check("llama.cpp (GGUF)", "llama-server", required=True),
        # MLX runtimes are optional and Apple-Silicon-only.
        _runtime_check("MLX text (mlx-lm)", "mlx_lm.server", required=False, relevant=mlx_relevant),
        _runtime_check("MLX vision (mlx-vlm)", "mlx_vlm.server", required=False, relevant=mlx_relevant),
    ]


def _host_label(url: str) -> str:
    """A base URL as a place: ``http://msai:1234`` -> ``msai:1234``.

    An unparseable string falls back to itself: an operator typed it, so show what heim was
    actually given rather than something tidier that isn't it.
    """
    try:
        return urlsplit(url).netloc or url
    except ValueError:
        return url


def _connect_failure(exc: Exception) -> str:
    """Describe a connection-level failure by its root cause rather than httpx's wrapper text.

    httpx flattens "that name doesn't resolve" and "nothing is listening there" into the same
    ``ConnectError``, but they send the user to completely different places (their DNS/hosts entry
    vs the remote's server process). The concrete ``OSError`` survives on the exception chain, so
    classify by type — errno *text* is platform-worded and not worth matching on.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, socket.gaierror):
            return "host name does not resolve (DNS lookup failed)"
        if isinstance(cur, ConnectionRefusedError):
            return "connection refused (nothing listening on that port)"
        # __context__ as well as __cause__: httpcore re-raises without chaining explicitly.
        cur = cur.__cause__ or cur.__context__
    return f"connection failed ({exc})"


def check_upstream(settings: Settings) -> list[Check]:
    """Check the backend the models actually run on: a remote ``/v1``, or heim's own runtimes.

    One row in **both** modes, and it must stand on its own: system health is the only place heim
    names its backend, so this row alone has to answer "which host is this talking to, and is it
    answering". Staying silent in local mode would leave the first half unanswered — the same reason
    the MLX rows say "not applicable on this platform" instead of vanishing.

    Under ``serve --upstream`` the remote is the hardest dependency heim has — if it's down nothing
    generates at all — so it's probed for real: reachable reports what the remote serves and which
    model it has resident, unreachable is a ``fail`` that says *how* it failed and names the URL to
    go and look at. Never raises: a doctor run reports a failed check, it doesn't traceback.
    """
    upstream = (settings.upstream or "").strip()
    if not upstream:
        return [
            Check(
                name="Backend",
                status=CheckStatus.ok,
                detail="Local runtime - heim spawns the model process on this machine",
            )
        ]
    # Same normalization as UpstreamManager: accept the URL with or without a trailing /v1.
    base = upstream.rstrip("/").removesuffix("/v1").rstrip("/")
    label = f"Upstream {_host_label(base)}"
    hint = f"Check the server at {base} is up (`curl {base}/v1/models`); or drop --upstream to run models locally."

    def _fail(detail: str) -> list[Check]:
        return [Check(name="Backend", status=CheckStatus.fail, detail=f"{label} - {detail}", hint=hint)]

    try:
        resp = httpx.get(f"{base}/v1/models", timeout=_UPSTREAM_TIMEOUT)
        resp.raise_for_status()
    except httpx.TimeoutException:
        # Distinct from refused: something may well be there, it just never answered in time.
        return _fail(f"no answer within {_UPSTREAM_TIMEOUT:.0f}s (unreachable, or busy loading a model)")
    except httpx.HTTPStatusError as exc:
        return _fail(f"answered HTTP {exc.response.status_code} on /v1/models (wrong URL, or auth required?)")
    except httpx.HTTPError as exc:
        return _fail(_connect_failure(exc))
    except Exception as exc:  # never let a health check be the thing that crashes `heim doctor`
        return _fail(f"probe failed: {exc!r}")

    try:
        rows = server.parse_model_listing(resp.json())
    except ValueError:
        # Answered, but with something that isn't a model listing - a proxy error page, an HTML
        # login screen, a non-OpenAI service on that port. Name the content type: it's the clue.
        ctype = resp.headers.get("content-type", "unknown")
        return _fail(f"answered /v1/models with {ctype}, not an OpenAI model listing")
    except Exception as exc:
        return _fail(f"probe failed: {exc!r}")

    if not rows:
        return [
            Check(
                name="Backend",
                status=CheckStatus.warn,
                detail=f"{label} - reachable, but serves no models",
                hint="The remote answered an empty /v1/models; nothing can be selected until it loads one.",
            )
        ]
    # `loaded` only comes from llama-server router mode's per-model status; other servers (LM
    # Studio, mlx-lm, another heim) never report it, so its absence isn't a fault.
    resident = next((r.name for r in rows if r.loaded), None)
    served = f"reachable, {len(rows)} model{'' if len(rows) == 1 else 's'}"
    detail = f"{label} - {served}, loaded: {resident}" if resident else f"{label} - {served} (none reported loaded)"
    return [Check(name="Backend", status=CheckStatus.ok, detail=detail)]


def check_library(settings: Settings) -> list[Check]:
    """Check the library roots and what's in them."""
    if not library_ops.configured(settings):
        return [
            Check(
                name="Libraries",
                status=CheckStatus.fail,
                detail="not configured",
                hint="Set HEIM_LIBRARY_ROOT to your library path, or run `heim project init`.",
            )
        ]
    checks: list[Check] = []
    lib_roots = library_ops.roots(settings)
    missing = [r for r in lib_roots if not r.is_dir()]
    detail = "\n".join(f"{r}" + ("" if r.is_dir() else " (not mounted)") for r in lib_roots)
    checks.append(
        Check(
            name="Libraries",
            status=CheckStatus.ok if not missing else CheckStatus.warn,
            detail=detail,
            hint=None if not missing else "A library isn't mounted; models in the others still work.",
        )
    )

    models = [m for m in library_ops.scan() if m.generative and not m.is_ollama]
    by_format: dict[str, int] = {}
    for m in models:
        by_format[m.model_format.value] = by_format.get(m.model_format.value, 0) + 1
    summary = ", ".join(f"{n} {fmt}" for fmt, n in sorted(by_format.items())) if models else "none"
    checks.append(
        Check(
            name="Runnable models",
            status=CheckStatus.ok if models else CheckStatus.warn,
            detail=f"{len(models)} ({summary})",
            hint=None
            if models
            else "Pull one with `heim library pull` (or `heim library sources` to see local caches).",
        )
    )
    return checks


def check_project(settings: Settings) -> list[Check]:
    """Check the current project (if any) and the effective default model.

    A project is optional (free-play is valid), so its rows only appear when a ``heim.toml``
    is present. The **default model** row is always shown when one is resolvable — the project's
    model, else the machine default (``heim config set model``) — since that's what ``heim chat``
    and ``serve --ui`` load without an explicit name.
    """
    proj = project_ops.load()
    checks: list[Check] = []
    if proj is not None:
        checks.append(Check(name="Project (heim.toml)", status=CheckStatus.ok, detail="found"))
        # A project that lists @shared but whose library_root is unset silently drops the shared
        # archive (see library.roots): it runs from its own libraries, but the drive's models are
        # invisible with no error. Warn so it's not a mystery.
        if library_ops.SHARED_TOKEN in proj.libraries and "library_root" not in settings.model_fields_set:
            checks.append(
                Check(
                    name="Shared library (@shared)",
                    status=CheckStatus.warn,
                    detail="listed in this project but unreachable — HEIM_LIBRARY_ROOT is not set",
                    hint="Set HEIM_LIBRARY_ROOT (e.g. export it in your shell profile) so @shared resolves; "
                    "until then this project runs only from its own libraries.",
                )
            )

    # The effective default model: project model > machine default (settings.default_model).
    default_model = project_ops.resolve_model(None, proj)
    if default_model is not None:
        resolved = library_ops.find(default_model)
        from_project = bool(proj and proj.model)
        detail = (
            default_model + ("" if from_project else " (machine default)") + ("" if resolved else " — not in library")
        )
        checks.append(
            Check(
                name="Default model",
                status=CheckStatus.ok if resolved else CheckStatus.warn,
                detail=detail,
                hint=None if resolved else f"Pull it: `heim library pull huggingface {default_model}`.",
            )
        )
    elif proj is not None:
        # A project usually pins a model; flag when it doesn't. (No project + no machine default
        # is plain free-play — nothing to report.)
        checks.append(
            Check(
                name="Default model",
                status=CheckStatus.warn,
                detail="not set",
                hint="Set one in heim.toml, or a machine default: `heim config set model <name>`.",
            )
        )

    # Effective tools: the resolved mcp.json servers (global + project). Shown whenever any exist.
    servers = mcpservers.resolve()
    if servers:
        names = ", ".join(s.name for s in servers)
        checks.append(Check(name=MCP_GROUP, status=CheckStatus.ok, detail=f"{len(servers)} ({names})"))
    return checks


def run_checks(settings: Settings | None = None) -> DoctorReport:
    """Run every health check and return the aggregated report."""
    conf = settings or get_settings()
    checks = [
        *check_platform(),
        *check_runtimes(),
        # After the runtime rows: it says whether those binaries are even in play (upstream mode
        # runs the models elsewhere) before the report moves on to the library and the project.
        *check_upstream(conf),
        *check_library(conf),
        *check_project(conf),
    ]
    return DoctorReport(checks=checks)
