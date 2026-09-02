"""System health checks for ``stabbur doctor``.

Each check is a pure function returning a :class:`Check`, so the whole report is
testable without a terminal. The CLI (:func:`stabbur.cli.doctor`) renders them.

Checks cover the things that make ``stabbur run/serve`` actually work: the backend the
models run on (local runtimes or a remote ``/v1``), the runtime binaries stabbur spawns
(llama.cpp / MLX), the library location, what's in it, and the current project manifest.

The report has a **shape**, not just a length. Two rows sit at the top level — ``Backend``
(is the thing that runs models alive) and ``Model`` (which one is in play) — because those
are the two facts someone opens a health readout to learn. Everything else is detail, and
nests under one of the group parents below via :attr:`Check.group`, so a consumer renders
three or four collapsed headings instead of a wall. The hierarchy travels in the payload;
no consumer re-derives it from names.
"""

import socket
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from stabbur import __version__ as stabbur_version
from stabbur import host, mcpservers, runtime, server
from stabbur import library as library_ops
from stabbur import project as project_ops
from stabbur.config import Settings, get_settings

# Timeout for the upstream health probe. Deliberately NOT UpstreamManager._LISTING_TIMEOUT (15s):
# that budget exists so a llama-server busy generating is never mis-reported as an outage on the
# serving path, and it's paid at most once per model swap. This probe is different — `stabbur doctor`
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


# The parent rows. A report a person actually reads answers two things at the top — is the backend
# alive, and which model is in play — and files the rest under a handful of headings they only open
# when they want it. Each constant is the ``name`` its children carry in ``Check.group``, stated once
# here because the emitters are spread across this module and the API layer (see _mcp_checks): two
# spellings of the same heading silently orphans every child under it.
RUNTIMES_GROUP = "Runtimes"
LIBRARY_GROUP = "Library"
PROJECT_GROUP = "Project"
MCP_GROUP = "Tools (MCP)"

# The two top-level rows, likewise named once (the serving layer replaces neither, but it does have
# to know MODEL_ROW is the row it is feeding).
BACKEND_ROW = "Backend"
MODEL_ROW = "Model"


class LoadedModel(BaseModel):
    """What a *serving* stabbur knows about the model resident right now.

    Passed into :func:`run_checks` by the API layer, which holds the ``ServerManager`` /
    ``UpstreamManager`` this module has no access to. Absent (``None``) on the CLI, where there is
    no runtime and the best answer is the model that *would* load — see :func:`check_model`.
    """

    name: str | None = None
    n_ctx: int | None = None  # context window it was loaded with (None = the runtime's own default)
    error: str | None = None  # why the runtime died, when nothing is loaded because it fell over
    # Whether this server fronts a remote /v1 rather than spawning runtimes itself. The failure
    # the ``error`` above records is a different event in each mode — a local process exited, or a
    # remote never answered — and only one of them is fixed by picking a model again.
    upstream: bool = False


class BackendProbe(BaseModel):
    """The ``Backend`` row, plus the one thing the probe learned that another check needs.

    ``check_upstream`` is the only code that talks to the remote, and the remote's ``/v1/models``
    is the only place the *resident* model name exists — but that fact belongs to the ``Model``
    row, not to this one. Rather than probe twice (a doctor run is interactive, and the UI polls
    it) or state the model in both rows, the probe hands it out here and :func:`run_checks` routes
    it to the check that owns it.
    """

    checks: list[Check]
    resident: str | None = None


class DoctorReport(BaseModel):
    """The full set of checks plus a rolled-up worst status."""

    checks: list[Check]
    # Which stabbur produced this report. Not a check - there is no failing version - but it belongs
    # with the answer to "what is this stabbur", and it is the only way to tell a stale browser bundle
    # from a current one without reading package metadata by hand.
    version: str = stabbur_version

    @property
    def status(self) -> CheckStatus:
        """The worst status across all checks (fail > warn > ok)."""
        if any(c.status is CheckStatus.fail for c in self.checks):
            return CheckStatus.fail
        if any(c.status is CheckStatus.warn for c in self.checks):
            return CheckStatus.warn
        return CheckStatus.ok


def _runtime_check(
    name: str, binary: str, *, required: bool, relevant: bool = True, why: str = "not applicable on this platform"
) -> Check:
    """Check that a runtime binary is on PATH.

    Args:
        name: Human label for the check.
        binary: Executable to look for.
        required: If missing, ``fail`` (a needed runtime) vs ``warn`` (optional).
        relevant: If false (e.g. MLX off Apple Silicon), report ``ok`` and say why.
        why: What to show when it isn't relevant.
    """
    path = runtime.resolve_binary(binary)
    if path is not None:
        return Check(name=name, status=CheckStatus.ok, detail=path, group=RUNTIMES_GROUP)
    if not relevant:
        return Check(name=name, status=CheckStatus.ok, detail=why, group=RUNTIMES_GROUP)
    return Check(
        name=name,
        status=CheckStatus.fail if required else CheckStatus.warn,
        detail=f"{binary!r} not found (checked stabbur's environment and PATH)",
        hint=runtime._INSTALL_HINTS.get(binary),
        group=RUNTIMES_GROUP,
    )


def check_runtimes(*, upstream: bool = False) -> list[Check]:
    """Check the model-runtime binaries stabbur spawns, under one collapsible parent.

    The parent's detail is the OS/arch that used to be a top-level ``Platform`` row of its own.
    The platform was only ever there as context for these rows — "not applicable on this platform"
    means nothing without it, and nothing else in the report reads differently on Linux — so as the
    heading of the group it explains it is stated once, in the one place it is needed, and costs no
    row at the top level.

    ``upstream`` (``serve --upstream``) makes every one of them irrelevant: models run on the
    remote and stabbur spawns nothing here, so a missing local binary is a fact about a machine
    that isn't running the model. Reporting it as a warning sends people to install runtimes they
    will never use, and buries the one row that does matter — whether the remote is reachable.
    """
    mlx_relevant = host.is_apple_silicon() and not upstream
    remote = "not used — models run on the upstream"
    return [
        Check(
            name=RUNTIMES_GROUP,
            status=CheckStatus.ok,
            detail=f"{host.os_label()} · upstream" if upstream else host.os_label(),
        ),
        # GGUF is the cross-platform backbone; without llama-server nothing GGUF runs *locally*.
        _runtime_check("llama.cpp (GGUF)", "llama-server", required=True, relevant=not upstream, why=remote),
        # MLX runtimes are optional and Apple-Silicon-only.
        _runtime_check("MLX text (mlx-lm)", "mlx_lm.server", required=False, relevant=mlx_relevant, why=remote),
        _runtime_check("MLX vision (mlx-vlm)", "mlx_vlm.server", required=False, relevant=mlx_relevant, why=remote),
    ]


def _host_label(url: str) -> str:
    """A base URL as a place: ``http://gpu-box:8080`` -> ``gpu-box:8080``.

    An unparseable string falls back to itself: an operator typed it, so show what stabbur was
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


def check_upstream(settings: Settings) -> BackendProbe:
    """Check the backend the models actually run on: a remote ``/v1``, or stabbur's own runtimes.

    One row in **both** modes, and it must stand on its own: it is the report's headline answer to
    "is the thing that runs models alive", so it has to name which host that is and say whether it
    answered. Staying silent in local mode would leave the first half unanswered — the same reason
    the MLX rows say "not applicable on this platform" instead of vanishing. It names the backend;
    it does not explain what one is. "Local runtime - stabbur spawns the model process on this machine"
    told the reader something they did not ask and cost two wrapped lines at the top of the menu.

    Under ``serve --upstream`` the remote is the hardest dependency stabbur has — if it's down nothing
    generates at all — so it's probed for real: reachable reports what the remote serves, unreachable
    is a ``fail`` that says *how* it failed and names the URL to go and look at. The resident model
    the probe sees is returned rather than stated here — it is the ``Model`` row's fact, and a fact
    stated in two rows is a fact that will one day disagree with itself. Never raises: a doctor run
    reports a failed check, it doesn't traceback.
    """
    upstream = (settings.upstream or "").strip()
    if not upstream:
        return BackendProbe(
            checks=[Check(name=BACKEND_ROW, status=CheckStatus.ok, detail="Local runtime on this machine")]
        )
    # Same normalization as UpstreamManager: accept the URL with or without a trailing /v1.
    base = upstream.rstrip("/").removesuffix("/v1").rstrip("/")
    label = f"Upstream {_host_label(base)}"
    hint = f"Check the server at {base} is up (`curl {base}/v1/models`); or drop --upstream to run models locally."

    def _fail(detail: str) -> BackendProbe:
        return BackendProbe(
            checks=[Check(name=BACKEND_ROW, status=CheckStatus.fail, detail=f"{label} - {detail}", hint=hint)]
        )

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
    except Exception as exc:  # never let a health check be the thing that crashes `stabbur doctor`
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
        return BackendProbe(
            checks=[
                Check(
                    name=BACKEND_ROW,
                    status=CheckStatus.warn,
                    detail=f"{label} - reachable, but serves no models",
                    hint="The remote answered an empty /v1/models; nothing can be selected until it loads one.",
                )
            ]
        )
    # `loaded` only comes from llama-server router mode's per-model status; other servers (LM
    # Studio, mlx-lm, another stabbur) never report it, so its absence isn't a fault.
    resident = next((r.name for r in rows if r.loaded), None)
    detail = f"{label} - reachable, {len(rows)} model{'' if len(rows) == 1 else 's'}"
    return BackendProbe(checks=[Check(name=BACKEND_ROW, status=CheckStatus.ok, detail=detail)], resident=resident)


def check_library(settings: Settings) -> list[Check]:
    """Check the library roots and what's in them, under one collapsible parent.

    The **parent** is where the roots go, because "is the drive there" is the one library fact worth
    seeing without opening anything — a library on an ejected disk is the failure this section
    exists to catch, and it reads at a glance as a path with ``(not mounted)`` after it. What is
    *inside* the roots is the detail a reader goes looking for, so it nests.
    """
    if not library_ops.configured(settings):
        return [
            Check(
                name=LIBRARY_GROUP,
                status=CheckStatus.fail,
                detail="not configured",
                hint="Set STABBUR_LIBRARY_ROOT to your library path, or run `stabbur project init`.",
            )
        ]
    checks: list[Check] = []
    lib_roots = library_ops.roots(settings)
    missing = [r for r in lib_roots if not r.is_dir()]
    detail = "\n".join(f"{r}" + ("" if r.is_dir() else " (not mounted)") for r in lib_roots)
    checks.append(
        Check(
            name=LIBRARY_GROUP,
            status=CheckStatus.ok if not missing else CheckStatus.warn,
            detail=detail,
            hint=None if not missing else "A library isn't mounted; models in the others still work.",
        )
    )

    models = [m for m in library_ops.scan() if m.generative and not m.is_ollama]
    if not models:
        checks.append(
            Check(
                name="Runnable models",
                status=CheckStatus.warn,
                detail="0 (none)",
                hint="Pull one with `stabbur library pull` (or `stabbur library sources` to see local caches).",
                group=LIBRARY_GROUP,
            )
        )
        return checks

    # "Runnable" has to mean runnable *on this machine*. Counting every generative GGUF/MLX build
    # called a library of MLX-only models runnable on a box with no mlx_lm.server installed — a
    # green doctor followed by `stabbur chat` dying on "not found on PATH", which is exactly the
    # thing a pre-flight exists to catch. So each model's format is crossed against the binary it
    # would actually spawn (see runtime.runtime_binary), and only the ones with that binary
    # present are counted.
    # Under `--upstream` the local runtimes never spawn, so "runnable on this machine" is not the
    # question being asked — the models on the drive are inventory, and what runs them lives
    # elsewhere. Partitioning by a local binary there warns about a limit that does not exist.
    upstream = bool((settings.upstream or "").strip())
    runnable, blocked = (models, {}) if upstream else _partition_by_runtime(models)
    by_format: dict[str, int] = {}
    for m in runnable:
        by_format[m.model_format.value] = by_format.get(m.model_format.value, 0) + 1
    summary = ", ".join(f"{n} {fmt}" for fmt, n in sorted(by_format.items())) if runnable else "none"
    detail = f"{len(runnable)} ({summary})" if not blocked else f"{len(runnable)} of {len(models)} ({summary})"
    checks.append(
        Check(
            name="Runnable models",
            status=CheckStatus.ok if runnable and not blocked else CheckStatus.warn,
            detail=detail,
            hint=None if not blocked else _missing_runtime_hint(blocked),
            group=LIBRARY_GROUP,
        )
    )
    return checks


def _partition_by_runtime(
    models: list[library_ops.LibraryModel],
) -> tuple[list[library_ops.LibraryModel], dict[str, int]]:
    """Split models into those whose runtime binary is installed and a count of those blocked by binary.

    The second half is keyed by the *missing* binary, because that is what a hint has to name:
    "install this one thing and N of your models start working".
    """
    runnable: list[library_ops.LibraryModel] = []
    blocked: dict[str, int] = {}
    present: dict[str, bool] = {}  # resolve each binary once, not once per model
    for m in models:
        binary = runtime.runtime_binary(m)
        if binary is None:
            continue  # not runnable by stabbur at all; it was never a candidate
        if binary not in present:
            present[binary] = runtime.resolve_binary(binary) is not None
        if present[binary]:
            runnable.append(m)
        else:
            blocked[binary] = blocked.get(binary, 0) + 1
    return runnable, blocked


def _missing_runtime_hint(blocked: dict[str, int]) -> str:
    """Say which binary is missing and how many models are waiting on it."""
    parts = [
        f"{n} model{'' if n == 1 else 's'} need{'s' if n == 1 else ''} {binary!r}, which isn't installed"
        for binary, n in sorted(blocked.items())
    ]
    return "; ".join(parts) + " — see the Runtimes rows above for how to install it."


def check_model(settings: Settings, *, loaded: LoadedModel | None = None, resident: str | None = None) -> list[Check]:
    """Which model is in play — the one running right now, else the one that would.

    ONE row for a question with two sources. A serving stabbur knows what is actually resident
    (``loaded`` from its manager, ``resident`` from the upstream's own listing when stabbur itself has
    selected nothing yet); the CLI has no runtime and can only name the default ``stabbur chat`` would
    load — the project's model, else the machine default. Those are the same question asked at
    different moments, so they share a row and the detail says which of the two it is. Two rows
    ("Model" and "Default model") would put the model in the menu twice and start disagreeing the
    instant someone loads one that isn't the default.

    Args:
        settings: Effective settings (the machine default lives here).
        loaded: What a serving stabbur has resident, or ``None`` on the CLI.
        resident: The model an upstream reports loaded, from :func:`check_upstream`'s probe.
    """
    proj = project_ops.load()
    default_model = project_ops.resolve_model(None, proj)
    # stabbur's own selection wins over the remote's: under an upstream both are true, and the one
    # stabbur will actually name in the next request is the one a reader is asking about.
    running = (loaded.name if loaded else None) or resident
    if running:
        # n_ctx belongs to a local runtime we spawned; it says nothing about a remote's presets.
        ctx = f", {loaded.n_ctx:,} ctx" if loaded and loaded.name == running and loaded.n_ctx else ""
        return [Check(name=MODEL_ROW, status=CheckStatus.ok, detail=f"{running} - loaded{ctx}")]

    if loaded is not None:
        # Serving, with nothing resident. A runtime that fell over is the actionable case and says
        # so; an idle server is simply waiting to be asked and stays green — a health dot that turns
        # amber every time you open a fresh tab teaches people to ignore it.
        if loaded.error:
            # The hint has to match the mode, because the two failures have nothing in common:
            # locally a process stabbur spawned exited and picking again respawns it, while under
            # --upstream nothing local ever ran and the fix is on the other host. Telling an
            # upstream user to "restart the runtime" points them at a runtime that does not exist.
            hint = (
                "Nothing runs on this machine under --upstream; see the Backend row for the host that answers."
                if loaded.upstream
                else "The runtime exited; pick a model again to restart it."
            )
            return [Check(name=MODEL_ROW, status=CheckStatus.fail, detail=f"none loaded - {loaded.error}", hint=hint)]
        detail = "none loaded" + (f" (default: {default_model})" if default_model else "")
        return [Check(name=MODEL_ROW, status=CheckStatus.ok, detail=detail)]

    if default_model is None:
        # A project usually pins a model; flag when it doesn't. (No project + no machine default is
        # plain free-play — nothing to report.)
        if proj is None:
            return []
        return [
            Check(
                name=MODEL_ROW,
                status=CheckStatus.warn,
                detail="not set",
                hint="Set one in stabbur.toml, or a machine default: `stabbur config set model <name>`.",
            )
        ]
    resolved = library_ops.find(default_model)
    source = "project default" if (proj and proj.model) else "machine default"
    if not resolved:
        return [
            Check(
                name=MODEL_ROW,
                status=CheckStatus.warn,
                detail=f"{default_model} - {source}, not in library",
                hint=f"Pull it: `stabbur library pull huggingface {default_model}`.",
            )
        ]
    # Present is not the same as runnable: the default model can sit right there on the drive and
    # still be unstartable because the runtime its format needs isn't installed. That is precisely
    # what `stabbur chat` fails with a moment later, so say it here instead of reporting ok.
    missing = _missing_binary(resolved[0])
    return [
        Check(
            name=MODEL_ROW,
            status=CheckStatus.ok if missing is None else CheckStatus.warn,
            detail=f"{default_model} - {source}" + ("" if missing is None else f", but {missing!r} isn't installed"),
            hint=None if missing is None else runtime._INSTALL_HINTS.get(missing),
        )
    ]


def _missing_binary(model: library_ops.LibraryModel) -> str | None:
    """The runtime binary ``model`` needs and this machine lacks, or ``None`` when it can run."""
    binary = runtime.runtime_binary(model)
    if binary is None or runtime.resolve_binary(binary) is not None:
        return None
    return binary


def check_project(settings: Settings) -> list[Check]:
    """Check the current project (if any) and the tools it resolves to.

    A project is optional (free-play is valid), so its parent row only appears when a ``stabbur.toml``
    is in scope — the nearest one at or above the working directory (:func:`stabbur.project.discover`)
    — and what nests under it is what a project can get *wrong*, which today is exactly one thing: a
    ``@shared`` library that doesn't resolve. The model a project pins is not here; it is the ``Model``
    row's business (:func:`check_model`), whichever manifest it came from.
    """
    proj = project_ops.load()
    checks: list[Check] = []
    if proj is not None:
        # Name the manifest when it came from a parent directory: `doctor` is where you go to find
        # out what stabbur thinks is true here, and "found" without a path is the wrong answer when
        # the project you are bound to isn't the one you can see.
        found = proj.manifest_path
        above = found is not None and found.resolve().parent != Path.cwd()
        detail = f"{found} found" if above else "stabbur.toml found"
        checks.append(Check(name=PROJECT_GROUP, status=CheckStatus.ok, detail=detail))
        # A project that lists @shared but whose library_root is unset silently drops the shared
        # archive (see library.roots): it runs from its own libraries, but the drive's models are
        # invisible with no error. Warn so it's not a mystery.
        if library_ops.SHARED_TOKEN in proj.libraries and "library_root" not in settings.model_fields_set:
            checks.append(
                Check(
                    name="Shared library (@shared)",
                    status=CheckStatus.warn,
                    detail="listed in this project but unreachable — STABBUR_LIBRARY_ROOT is not set",
                    hint="Set STABBUR_LIBRARY_ROOT (e.g. export it in your shell profile) so @shared resolves; "
                    "until then this project runs only from its own libraries.",
                    group=PROJECT_GROUP,
                )
            )

    # Effective tools: the resolved mcp.json servers (global + project). Shown whenever any exist.
    # A count, not a list: which servers they are is one row down, and a parent that recites its own
    # children says the same thing twice and grows a comma-separated sentence as they multiply.
    servers = mcpservers.resolve()
    if servers:
        checks.append(
            Check(
                name=MCP_GROUP,
                status=CheckStatus.ok,
                detail=f"{len(servers)} server{'' if len(servers) == 1 else 's'}",
            )
        )
    return checks


def run_checks(settings: Settings | None = None, loaded: LoadedModel | None = None) -> DoctorReport:
    """Run every health check and return the aggregated report.

    Order is the reading order: the two rows a person opens the menu for come first (is the backend
    alive, what is loaded), then the groups they open only when they want the detail.

    Args:
        settings: Effective settings; the process defaults when omitted.
        loaded: What a serving stabbur has resident right now. ``None`` on the CLI, where there is no
            runtime to ask and the ``Model`` row names the default instead.
    """
    conf = settings or get_settings()
    backend = check_upstream(conf)
    checks = [
        *backend.checks,
        *check_model(conf, loaded=loaded, resident=backend.resident),
        *check_runtimes(upstream=bool((conf.upstream or "").strip())),
        *check_library(conf),
        *check_project(conf),
    ]
    return DoctorReport(checks=checks)
