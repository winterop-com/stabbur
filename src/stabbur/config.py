"""Application configuration.

``stabbur.toml`` in the working directory is the primary config source: it holds
both the library location (``library_root``) and the project/assistant manifest
(``[project]`` / ``[voice]``, read separately by :mod:`stabbur.project`; tools live in
``.mcp.json``, see :mod:`stabbur.mcpservers`). Every
value can still be overridden per machine with a ``STABBUR_*`` environment
variable; ``.env`` remains an optional low-priority fallback. Below that sits the
durable **machine config** (:mod:`stabbur.userconfig`, ``~/.config/stabbur/config.toml``),
written by ``stabbur config`` / ``stabbur setup`` — the persistent per-machine default
(library location, default model) so a fresh box needs no shell export.

Precedence (high to low): CLI args, ``STABBUR_*`` env vars, ``stabbur.toml``, ``.env``,
machine config.

Also here, because it is configuration rather than state: :func:`declared_backends`, which turns
``[[backends]]`` tables plus ``--upstream`` values into the named backends the serving layer can
hold (ROADMAP, "Multiple backends at once").
"""

import ipaddress
import json
import os
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

if TYPE_CHECKING:  # `stabbur.backends` imports `stabbur.library`, which imports this module
    from stabbur.backends import BackendSpec


class _StabburTomlSource(PydanticBaseSettingsSource):
    """Feed machine settings from the shared ``stabbur.toml`` parse (:func:`stabbur.project.read_raw`).

    ``stabbur.toml`` holds both machine settings (these) and the portable project manifest
    (``[project]`` etc., read by :mod:`stabbur.project`). Routing this source through the same
    ``read_raw`` parser the manifest uses means the file is parsed by **one** code path, so a
    malformed file raises a single, clean ``ProjectError`` rather than crashing differently in
    each reader (A1). Top-level scalar/list keys map to :class:`Settings` fields; the manifest
    tables (``project``/``mcp``/``voice``/``libraries``) are ignored here (``extra="ignore"``).
    """

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: ARG002
        return None, field_name, False  # unused: __call__ returns the whole dict below

    def __call__(self) -> dict[str, Any]:
        from stabbur import project  # noqa: PLC0415 - lazy: avoid an import cycle at module load

        return project.read_raw()


class _MachineConfigSource(PydanticBaseSettingsSource):
    """Feed settings from the durable machine config (:func:`stabbur.userconfig.read`).

    The lowest-priority real source: per-machine defaults (library location, default model)
    written by ``stabbur config`` / ``stabbur setup``. Its TOML keys are already :class:`Settings`
    field names, so the parsed dict maps directly; unknown keys are ignored (``extra="ignore"``).
    """

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: ARG002
        return None, field_name, False  # unused: __call__ returns the whole dict below

    def __call__(self) -> dict[str, Any]:
        from stabbur import userconfig  # noqa: PLC0415 - lazy, symmetry with _StabburTomlSource

        return userconfig.read()


def _default_lmstudio_dir() -> Path:
    """Return the first LM Studio model directory that exists, or the modern default."""
    candidates = [
        Path.home() / ".lmstudio" / "models",
        Path.home() / ".cache" / "lm-studio" / "models",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _default_runtime_state_dir() -> Path:
    """Ephemeral per-runtime state dir (pidfiles + logs), placed per the XDG spec.

    Runtime state is transient and machine-local (a pid means nothing elsewhere), so it belongs
    under ``$XDG_RUNTIME_DIR`` (user-private, cleared on logout — ideal for pidfiles) when set,
    else the cache dir (``$XDG_CACHE_HOME``, default ``~/.cache``). Not a library (it must never
    travel with the drive), and no longer ``~/.stabbur`` (which wasn't XDG-compliant).
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "stabbur" / "runtimes"
    cache = os.environ.get("XDG_CACHE_HOME")
    return (Path(cache) if cache else Path.home() / ".cache") / "stabbur" / "runtimes"


# `stabbur serve`'s default port. Deliberately unusual (and above the privileged range) to
# avoid the common dev-server ports; 2222 is sometimes an alternate SSH port, so a collision
# is reported rather than guessed around.
DEFAULT_SERVE_PORT = 2222


def _default_frontend_dir() -> Path:
    """Where the built SPA lives, for an installed package and for a checkout alike.

    Two locations, in priority order:

    1. ``stabbur/webui`` INSIDE the installed package. This is the one that matters for a real
       install: the wheel carries the built SPA as package data, so ``uvx stabbur serve --ui``
       has a UI. Before this existed the wheel shipped no frontend at all and ``/`` answered
       404 for every PyPI user, while every checkout worked - which is exactly why it went
       unnoticed.
    2. ``frontend/dist`` beside the source tree, for development. `uv run` from a checkout uses
       the live Vite build, so editing the SPA does not require re-packaging it.
    """
    packaged = Path(__file__).resolve().parent / "webui"
    if (packaged / "index.html").is_file():
        return packaged
    return Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


class Settings(BaseSettings):
    """Application settings — read from ``stabbur.toml`` first, then env vars.

    Top-level keys in ``stabbur.toml`` (e.g. ``library_root = "/path/to/your/library"``)
    map directly to these fields; the ``[project]`` / ``[voice]`` tables are
    ignored here and read by :mod:`stabbur.project`. A ``STABBUR_*`` environment
    variable (e.g. ``STABBUR_LIBRARY_ROOT``) overrides the file per machine.
    """

    model_config = SettingsConfigDict(
        env_prefix="STABBUR_",
        env_file=".env",
        extra="ignore",  # manifest tables ([project]/[voice]/…) coexist in stabbur.toml; ignore them here
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Order the sources: init args > env vars > stabbur.toml > .env > machine config > secrets.

        ``stabbur.toml`` is the primary config file, so it outranks ``.env``; real
        environment variables still win for genuine per-machine overrides. The machine
        config (:mod:`stabbur.userconfig`) sits at the bottom as the durable default a project
        or env var can override.
        """
        return (
            init_settings,
            env_settings,
            _StabburTomlSource(settings_cls),
            dotenv_settings,
            _MachineConfigSource(settings_cls),
            file_secret_settings,
        )

    app_name: str = "stabbur"
    debug: bool = False
    host: str = "127.0.0.1"
    # Web server (stabbur serve) port. A fixed default so the URL is stable across restarts
    # (bookmarks, the Chrome-extension origin, `stabbur chat --server`): a port that moves every
    # start is worse than one that occasionally collides — and a collision is reported, never
    # silently worked around. Override per run with --port, or per machine with
    # `stabbur config set port`.
    port: int = DEFAULT_SERVE_PORT

    # Serve the browser UI (single-page app) alongside the API. Resolved from this file rather
    # than the CWD, so ``serve --ui`` works from any directory. If it's missing (not built), the
    # API still runs.
    serve_ui: bool = False
    frontend_dir: Path = _default_frontend_dir()

    # Lock the server to a single model (no switching) — for the Chrome-extension
    # backend. Empty means free model switching.
    serve_model: str | None = None

    # Front a remote OpenAI-compatible /v1 (a llama-server in router mode, LM Studio, …)
    # instead of spawning local runtimes: stabbur's agent loop, tools, confirm gate, and the
    # web UI run here while the models run on the remote box. Set via `stabbur serve
    # --upstream <url>`. Empty means local runtimes (the default).
    upstream: str | None = None

    # Backends declared as ``[[backends]]`` tables (``name`` + optional ``url``) in
    # ``stabbur.toml`` or the machine config — several remotes and the local library in one
    # picker, where ``upstream`` above names exactly one remote. Read it through
    # :func:`declared_backends`, never directly: the entries are kept **raw** here and
    # validated there, for two reasons. A ``list[BackendSpec]`` field would need
    # ``stabbur.backends`` imported at module level, and that is a genuine import cycle
    # (backends -> library -> config -> backends, an ImportError on the first ``import
    # stabbur.library``). And this file is hand-edited: validating at the point of use turns a
    # typo into one readable message from the command that wanted a backend, instead of a
    # pydantic ValidationError on *every* stabbur command.
    backends: list[Any] = []

    # Bearer token required on the API (``/api``, ``/v1``, ``/models``) when set. Empty (the
    # default) disables auth — safe for the loopback-only default bind. ``stabbur serve`` auto-fills
    # a random one when it binds a non-loopback address, so exposing the server to the LAN never
    # leaves model control + tool execution unauthenticated (V-14). Clients send it as
    # ``Authorization: Bearer <token>``; the SPA also accepts it via a ``?token=`` URL param.
    auth_token: str = ""

    # Cross-origin origins allowed to call the API. Default is **same-origin only**
    # (empty list → no CORS middleware): the web UI is served by this same app, so
    # it needs no CORS, and a permissive default would let any website you visit
    # drive your local models + MCP tools from the browser. Add explicit origins
    # (e.g. the Chrome-extension origin, or a dev server) to allow cross-origin use.
    # ``NoDecode`` + the validator below accept a plain ``STABBUR_CORS_ORIGINS=a,b`` (or a single
    # origin) from the env — not only a JSON array — so following the Chrome-extension allow-list
    # advice doesn't crash every command (pydantic-settings otherwise JSON-decodes list env vars).
    cors_origins: Annotated[list[str], NoDecode] = []

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: Any) -> Any:
        """Accept a JSON array, a comma/space-separated string, or a native list."""
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                return json.loads(s)
            return [part.strip() for part in s.replace(",", " ").split() if part.strip()]
        return v

    # Internal port the model runtime (llama-server / mlx_lm.server) listens on;
    # the API proxies /v1 to it so the SPA stays single-origin. ``None`` (the
    # default) auto-picks a free port, so concurrent stabbur sessions don't collide;
    # set an int to pin it.
    runtime_port: int | None = None

    # How long to wait for a runtime to become ready before giving up. Generous
    # by default — big models (15-20 GB) can take minutes to load, especially on
    # a busy machine. A crashed runtime still fails fast (its process exits).
    runtime_load_timeout: int = 600

    # Spawn every configured MCP server eagerly at ``stabbur serve`` startup instead of deferring a
    # non-primary target's own servers to their first use. Off by default: startup then scales with the
    # targets actually used, not merely declared (a registry with many per-target bridges no longer pays
    # to spawn them all up front). Set ``STABBUR_EAGER_MCP=1`` to restore full eager spawning — a debugging
    # escape hatch that makes every target's tools live immediately (and surfaces a broken bridge at boot).
    eager_mcp: bool = False

    # Max seconds for a single MCP tool call in the agent loop before it's abandoned
    # (the model gets an error back and the loop continues). Bounds a wedged tool or
    # server — e.g. one shelling out to a command that never returns — so it can't stall
    # a chat or benchmark indefinitely. 0 disables the bound (wait forever).
    tool_timeout: float = 120.0

    # Default per-turn generation cap for /api/chat (the tool-aware agent loop) when the
    # request omits max_tokens. Bounds a small model that runs away on a hard tool question
    # and never emits a final answer (observed in the DHIS2 benchmark). Generous enough for
    # normal grounded answers; a client can still pass an explicit max_tokens to override,
    # and <= 0 disables the cap (unbounded). Only affects /api/chat — the raw /v1 proxy is
    # untouched, so power users hitting the runtime directly are never clipped.
    default_max_tokens: int = 4096

    # How long a pending write-confirmation (the /api/chat per-action gate) waits for the user
    # to approve or decline before auto-denying, in seconds. Set via ``STABBUR_CONFIRM_TIMEOUT``.
    # Bounds a confirm the user never answers (a closed tab, a walked-away session) so the gated
    # tool call fails safe instead of holding the agent loop open indefinitely.
    confirm_timeout: int = 300

    # The default library — a self-contained, portable model store (models + their
    # own metadata under ``.stabbur/``). Point ``STABBUR_LIBRARY_ROOT`` at it per machine
    # (e.g. an external drive). A project (``stabbur.toml``) can compose additional
    # libraries in front of this one (``libraries = [".stabbur/library", "@shared"]``),
    # where ``@shared`` resolves to this default; outside a project this is the only
    # library. **No default**: ``None`` means "not configured", and every consumer must
    # route through :func:`stabbur.library.roots` / :func:`stabbur.library.default_root`, which
    # raise ``LibraryNotConfigured`` rather than silently falling back to a ``./data`` dir.
    library_root: Path | None = None

    # Machine-default model, used outside a project (free-play) when no model is named on the
    # CLI. In a project, ``stabbur.toml``'s ``[project].model`` outranks this (a project pins its
    # own model); this is the fallback so ``stabbur chat`` / ``serve --ui`` have a model to load
    # without a project or an explicit name. Set it with ``stabbur config set model <name>``.
    default_model: str | None = None

    # A running ``stabbur serve`` for ``stabbur chat`` to attach to instead of spawning its own runtime
    # per call — so the model stays loaded across invocations (no multi-second reload each time).
    # An OpenAI ``/v1`` base URL, e.g. ``http://127.0.0.1:8000``. ``stabbur chat --server`` overrides;
    # set a default with ``stabbur config set server <url>`` (or ``STABBUR_CHAT_SERVER``).
    chat_server: str | None = None

    # Source stores to scan and back up from.
    ollama_models_dir: Path = Path.home() / ".ollama" / "models"
    lmstudio_models_dir: Path = _default_lmstudio_dir()

    # Optional Hugging Face token for gated/private repos. Falls back to the
    # standard HF_TOKEN / huggingface-cli login if unset.
    hf_token: str | None = None

    # Ephemeral, machine-local runtime state (one dir per spawned runtime: its pidfile-ish
    # ``meta.json`` + captured log). NOT library data — a pid means nothing on another machine,
    # so it lives under the XDG runtime/cache dir (see _default_runtime_state_dir), never a
    # library. The supervisor uses it to reap runtimes orphaned by a crashed stabbur (stabbur.supervisor).
    runtime_state_dir: Path = _default_runtime_state_dir()


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()


# --- declared backends -------------------------------------------------------
#
# Turning configuration into a list of named backends (ROADMAP, "Multiple backends at once").
# Declaration only: what *is* a backend here, not which one is loaded or how a model name
# resolves across them.

# The implicit backend for the machine's own library. The name is the qualifier in a
# ``model@local`` id, so it is a public string, not a label — and it is written into portable,
# committed places (a project's model reference, a bookmarked URL, the extension's config). That
# rules out deriving it from the hostname or the drive: the same ``stabbur.toml`` would then name a
# different backend on every machine, and renaming or swapping the drive would break the id.
# "local" instead says what the backend *is* — the runtimes this process spawns — which stays true
# wherever the library sits.
LOCAL_BACKEND_NAME = "local"

# Keys a ``[[backends]]`` table may set. Deliberately closed rather than ignored-if-unknown: the
# shape is two keys, and a typo'd ``ur1 =`` would otherwise declare a *local* backend (``url``
# absent means the library) that silently never reaches the host the user meant.
_BACKEND_ENTRY_KEYS = frozenset({"name", "url"})


class BackendDeclarationError(ValueError):
    """A ``[[backends]]`` entry or ``--upstream`` value that cannot become a backend.

    Its message is written to be printed at the user verbatim: every one of these comes from a
    hand-edited config file or a command line, where a typo must produce a readable line rather
    than a traceback.
    """


def _normalize_backend_url(url: str) -> str:
    """Strip a trailing slash and ``/v1`` from a backend URL.

    Mirrors :meth:`stabbur.server.UpstreamManager.__init__` (routes append their own ``/v1``
    paths), repeated rather than shared because config sits below both the server and the CLI and
    must not import either. Normalizing at declaration time is what makes ``http://msai:1234`` and
    ``http://msai:1234/v1`` one backend instead of two that would then collide on their name.
    """
    return url.strip().rstrip("/").removesuffix("/v1").rstrip("/")


def derive_backend_name(url: str) -> str:
    """Derive a backend name from a URL's host — ``http://msai:1234/v1`` becomes ``msai``.

    ``--upstream`` carries no name (that is the whole reason ``[[backends]]`` exists), so one is
    derived from the host: its first label, which is what a person calls the box —
    ``gpu-box.lan`` is "gpu-box". An IP literal keeps every digit; truncating ``127.0.0.1`` to
    ``127`` would name nothing.

    Args:
        url: The upstream URL, with or without a scheme.

    Returns:
        The derived backend name.

    Raises:
        BackendDeclarationError: If the URL has no host to derive a name from.
    """
    # A bare ``host:port`` parses as scheme + path, so re-parse it as a network location. Only
    # when the URL has no ``//`` at all: retrying a scheme'd-but-hostless URL would find a "host"
    # in its own scheme (``http:///v1`` -> "http") and name a backend after it.
    host = urlparse(url).hostname if "//" in url else urlparse(f"//{url}").hostname
    if not host:
        raise BackendDeclarationError(
            f"could not derive a backend name from {url!r} (no host); "
            f"declare it as a [[backends]] entry with an explicit name instead"
        )
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host.split(".")[0]
    return host


def _spec_from_entry(entry: Any, index: int) -> "BackendSpec":
    """Validate one raw ``[[backends]]`` table into a :class:`~stabbur.backends.BackendSpec`.

    Args:
        entry: The parsed TOML table.
        index: Its position in the list, so the error can point at the offending entry.

    Returns:
        The validated spec (``url`` normalized; absent ``url`` means the local library).

    Raises:
        BackendDeclarationError: If the entry is not a table, has no usable ``name``, has a
            non-string ``url``, or sets a key that is not ``name`` / ``url``.
    """
    from stabbur.backends import BackendSpec  # noqa: PLC0415 - lazy: see the `backends` field

    where = f"[[backends]] entry #{index + 1}"
    if not isinstance(entry, dict):
        raise BackendDeclarationError(f"{where} is not a table: expected `name = ...` (and an optional `url = ...`)")
    unknown = sorted(set(entry) - _BACKEND_ENTRY_KEYS)
    if unknown:
        raise BackendDeclarationError(f"{where} sets unknown key(s) {', '.join(unknown)}; only name and url are read")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BackendDeclarationError(f"{where} has no name; every backend needs one (it is the `model@backend` id)")
    url = entry.get("url")
    if url is not None and (not isinstance(url, str) or not url.strip()):
        raise BackendDeclarationError(f"{where} ({name}) has an unusable url; give an OpenAI /v1 base URL, or omit it")
    return BackendSpec(name=_checked_name(name, where), url=_normalize_backend_url(url) if url else None)


def _checked_name(name: str, where: str) -> str:
    """Reject a backend name that cannot be used as the qualifier in a ``model@backend`` id.

    Args:
        name: The declared or derived name.
        where: Human-readable origin, for the message.

    Returns:
        The name, stripped.

    Raises:
        BackendDeclarationError: If the name contains ``@`` (the separator itself) or whitespace.
    """
    clean = name.strip()
    # `model@backend` splits on the LAST `@`, so an `@` inside the backend name silently eats part
    # of it; whitespace makes the id unquotable on a command line and in a URL path.
    if "@" in clean or any(ch.isspace() for ch in clean):
        raise BackendDeclarationError(
            f"{where}: backend name {clean!r} may not contain '@' or whitespace — "
            f"it is the qualifier in a `model@backend` id"
        )
    return clean


def declared_backends(upstreams: Sequence[str] = (), settings: Settings | None = None) -> list["BackendSpec"]:
    """Every backend this configuration declares, in listing order.

    The one place configuration becomes a list of backends. Sources, each layered by the usual
    :class:`Settings` precedence (env > ``stabbur.toml`` > machine config) *before* they get here:

    1. the **local library**, implicit whenever one is configured — see :data:`LOCAL_BACKEND_NAME`;
    2. ``[[backends]]`` tables (``settings.backends``), in file order;
    3. ``--upstream`` values (``upstreams``), else the legacy single ``upstream`` setting.

    The three are concatenated rather than overriding one another because they answer different
    questions: ``[[backends]]`` is the durable declaration, ``--upstream`` is an ad-hoc addition
    for this run, and neither is a reason to stop being able to run the models on this machine.
    Order is listing order (local first: it is the one backend that works with no network); it is
    **not** a selection priority — what is loaded stays singular and is tracked elsewhere.

    Two flags naming the same place are one backend: URLs are normalized, and an ``--upstream``
    whose URL is already declared is dropped (keeping the configured name, so ``--upstream``-ing a
    host a project already declares is a no-op rather than a conflict). Two *different* URLs whose
    derived names collide is an error, never a silent pick — ``[[backends]]`` is where a name that
    a host cannot supply gets written.

    Args:
        upstreams: ``--upstream`` values from the command line, in the order given.
        settings: Settings to read; the cached process settings by default.

    Returns:
        The declared backends, names unique.

    Raises:
        BackendDeclarationError: On a malformed ``[[backends]]`` entry, an un-nameable URL, or a
            name declared twice.
    """
    from stabbur import library as library_ops  # noqa: PLC0415 - lazy: library imports this module
    from stabbur.backends import BackendSpec  # noqa: PLC0415 - lazy: see the `backends` field

    settings = settings or get_settings()
    specs: list[BackendSpec] = []
    by_name: dict[str, str] = {}  # name -> the URL (or "the local library") that claimed it

    def _claim(spec: BackendSpec, hint: str) -> None:
        """Take a name for ``spec``, or say who already holds it and how to settle it."""
        taken = by_name.get(spec.name)
        if taken is not None:
            raise BackendDeclarationError(
                f"two backends are named {spec.name!r}: {taken} and {spec.url or 'the local library'}. {hint}"
            )
        by_name[spec.name] = spec.url or "the local library"
        specs.append(spec)

    for index, entry in enumerate(settings.backends):
        _claim(_spec_from_entry(entry, index), "Give each [[backends]] entry a name of its own.")

    # A --upstream flag replaces STABBUR_UPSTREAM rather than adding to it: they are the same
    # single-remote switch, spelled on the command line and in the environment.
    urls = list(upstreams) or ([settings.upstream] if settings.upstream else [])
    declared_urls = {spec.url for spec in specs}
    for url in urls:
        base = _normalize_backend_url(url)
        if not base or base in declared_urls:
            continue
        declared_urls.add(base)
        # Named from the URL as written, not from the normalized base: stripping ``/v1`` can leave
        # a hostless URL that no longer parses the way the user typed it.
        _claim(
            BackendSpec(name=_checked_name(derive_backend_name(url.strip()), f"--upstream {url}"), url=base),
            "A --upstream name is its host's, so two ports on one host collide — "
            "declare them as [[backends]] entries with distinct names.",
        )

    # The local library leads the listing, but only if nothing has claimed its place: an explicit
    # entry always wins over an implicit one, whether it renamed the library (a `[[backends]]`
    # entry with no url) or merely took the name.
    local_declared = any(spec.url is None for spec in specs) or LOCAL_BACKEND_NAME in by_name
    if not local_declared and library_ops.configured(settings):
        specs.insert(0, BackendSpec(name=LOCAL_BACKEND_NAME))
    return specs


# Process-wide debug switch, flipped by the CLI's global ``--debug`` flag (or the
# ``STABBUR_DEBUG`` env var). When on, stabbur prints extra diagnostics — most usefully,
# it streams the model runtime's logs instead of discarding them.
_debug = False


def set_debug(on: bool) -> None:
    """Enable or disable process-wide debug output."""
    global _debug
    _debug = on


def debug_enabled() -> bool:
    """Whether debug output is on (via ``--debug`` or ``STABBUR_DEBUG``)."""
    return _debug or get_settings().debug


# CLI ``--runtime-port`` override; takes precedence over the setting when set.
_runtime_port_override: int | None = None


def set_runtime_port(port: int | None) -> None:
    """Pin the model-runtime port for this process (CLI ``--runtime-port``)."""
    global _runtime_port_override
    _runtime_port_override = port


def runtime_port_override() -> int | None:
    """The CLI ``--runtime-port`` override, or ``None`` if not set."""
    return _runtime_port_override


def pinned_runtime_port() -> int | None:
    """The pinned runtime port (``--runtime-port`` > setting), or ``None`` to auto-pick."""
    if _runtime_port_override is not None:
        return _runtime_port_override
    return get_settings().runtime_port
