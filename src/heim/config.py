"""Application configuration.

``heim.toml`` in the working directory is the primary config source: it holds
both the library location (``library_root``) and the project/assistant manifest
(``[project]`` / ``[voice]``, read separately by :mod:`heim.project`; tools live in
``.mcp.json``, see :mod:`heim.mcpservers`). Every
value can still be overridden per machine with a ``HEIM_*`` environment
variable; ``.env`` remains an optional low-priority fallback. Below that sits the
durable **machine config** (:mod:`heim.userconfig`, ``~/.config/heim/config.toml``),
written by ``heim config`` / ``heim setup`` — the persistent per-machine default
(library location, default model) so a fresh box needs no shell export.

Precedence (high to low): CLI args, ``HEIM_*`` env vars, ``heim.toml``, ``.env``,
machine config.
"""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class _HeimTomlSource(PydanticBaseSettingsSource):
    """Feed machine settings from the shared ``heim.toml`` parse (:func:`heim.project.read_raw`).

    ``heim.toml`` holds both machine settings (these) and the portable project manifest
    (``[project]`` etc., read by :mod:`heim.project`). Routing this source through the same
    ``read_raw`` parser the manifest uses means the file is parsed by **one** code path, so a
    malformed file raises a single, clean ``ProjectError`` rather than crashing differently in
    each reader (A1). Top-level scalar/list keys map to :class:`Settings` fields; the manifest
    tables (``project``/``mcp``/``voice``/``libraries``) are ignored here (``extra="ignore"``).
    """

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: ARG002
        return None, field_name, False  # unused: __call__ returns the whole dict below

    def __call__(self) -> dict[str, Any]:
        from heim import project  # noqa: PLC0415 - lazy: avoid an import cycle at module load

        return project.read_raw()


class _MachineConfigSource(PydanticBaseSettingsSource):
    """Feed settings from the durable machine config (:func:`heim.userconfig.read`).

    The lowest-priority real source: per-machine defaults (library location, default model)
    written by ``heim config`` / ``heim setup``. Its TOML keys are already :class:`Settings`
    field names, so the parsed dict maps directly; unknown keys are ignored (``extra="ignore"``).
    """

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: ARG002
        return None, field_name, False  # unused: __call__ returns the whole dict below

    def __call__(self) -> dict[str, Any]:
        from heim import userconfig  # noqa: PLC0415 - lazy, symmetry with _HeimTomlSource

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
    travel with the drive), and no longer ``~/.heim`` (which wasn't XDG-compliant).
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "heim" / "runtimes"
    cache = os.environ.get("XDG_CACHE_HOME")
    return (Path(cache) if cache else Path.home() / ".cache") / "heim" / "runtimes"


# `heim serve`'s default port. Deliberately unusual (and above the privileged range) to
# avoid the common dev-server ports; 2222 is sometimes an alternate SSH port, so a collision
# is reported rather than guessed around.
DEFAULT_SERVE_PORT = 2222


class Settings(BaseSettings):
    """Application settings — read from ``heim.toml`` first, then env vars.

    Top-level keys in ``heim.toml`` (e.g. ``library_root = "/path/to/your/library"``)
    map directly to these fields; the ``[project]`` / ``[voice]`` tables are
    ignored here and read by :mod:`heim.project`. A ``HEIM_*`` environment
    variable (e.g. ``HEIM_LIBRARY_ROOT``) overrides the file per machine.
    """

    model_config = SettingsConfigDict(
        env_prefix="HEIM_",
        env_file=".env",
        extra="ignore",  # manifest tables ([project]/[voice]/…) coexist in heim.toml; ignore them here
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
        """Order the sources: init args > env vars > heim.toml > .env > machine config > secrets.

        ``heim.toml`` is the primary config file, so it outranks ``.env``; real
        environment variables still win for genuine per-machine overrides. The machine
        config (:mod:`heim.userconfig`) sits at the bottom as the durable default a project
        or env var can override.
        """
        return (
            init_settings,
            env_settings,
            _HeimTomlSource(settings_cls),
            dotenv_settings,
            _MachineConfigSource(settings_cls),
            file_secret_settings,
        )

    app_name: str = "heim"
    debug: bool = False
    host: str = "127.0.0.1"
    # Web server (heim serve) port. A fixed default so the URL is stable across restarts
    # (bookmarks, the Chrome-extension origin, `heim chat --server`): a port that moves every
    # start is worse than one that occasionally collides — and a collision is reported, never
    # silently worked around. Override per run with --port, or per machine with
    # `heim config set port`.
    port: int = DEFAULT_SERVE_PORT

    # Serve the browser UI (single-page app) alongside the API. Defaults to the
    # ``frontend/dist`` that ships with the source tree (resolved from this file, not the
    # CWD) so ``serve --ui`` works from any directory — e.g. a globally-installed heim run
    # inside a project. If it's missing (not built), the API still runs.
    serve_ui: bool = False
    frontend_dir: Path = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

    # Lock the server to a single model (no switching) — for the Chrome-extension
    # backend. Empty means free model switching.
    serve_model: str | None = None

    # Front a remote OpenAI-compatible /v1 (a llama-server in router mode, LM Studio, …)
    # instead of spawning local runtimes: heim's agent loop, tools, confirm gate, and the
    # web UI run here while the models run on the remote box. Set via `heim serve
    # --upstream <url>`. Empty means local runtimes (the default).
    upstream: str | None = None

    # Bearer token required on the API (``/api``, ``/v1``, ``/models``) when set. Empty (the
    # default) disables auth — safe for the loopback-only default bind. ``heim serve`` auto-fills
    # a random one when it binds a non-loopback address, so exposing the server to the LAN never
    # leaves model control + tool execution unauthenticated (V-14). Clients send it as
    # ``Authorization: Bearer <token>``; the SPA also accepts it via a ``?token=`` URL param.
    auth_token: str = ""

    # Cross-origin origins allowed to call the API. Default is **same-origin only**
    # (empty list → no CORS middleware): the web UI is served by this same app, so
    # it needs no CORS, and a permissive default would let any website you visit
    # drive your local models + MCP tools from the browser. Add explicit origins
    # (e.g. the Chrome-extension origin, or a dev server) to allow cross-origin use.
    # ``NoDecode`` + the validator below accept a plain ``HEIM_CORS_ORIGINS=a,b`` (or a single
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
    # default) auto-picks a free port, so concurrent heim sessions don't collide;
    # set an int to pin it.
    runtime_port: int | None = None

    # How long to wait for a runtime to become ready before giving up. Generous
    # by default — big models (15-20 GB) can take minutes to load, especially on
    # a busy machine. A crashed runtime still fails fast (its process exits).
    runtime_load_timeout: int = 600

    # Spawn every configured MCP server eagerly at ``heim serve`` startup instead of deferring a
    # non-primary target's own servers to their first use. Off by default: startup then scales with the
    # targets actually used, not merely declared (a registry with many per-target bridges no longer pays
    # to spawn them all up front). Set ``HEIM_EAGER_MCP=1`` to restore full eager spawning — a debugging
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
    # to approve or decline before auto-denying, in seconds. Set via ``HEIM_CONFIRM_TIMEOUT``.
    # Bounds a confirm the user never answers (a closed tab, a walked-away session) so the gated
    # tool call fails safe instead of holding the agent loop open indefinitely.
    confirm_timeout: int = 300

    # The default library — a self-contained, portable model store (models + their
    # own metadata under ``.heim/``). Point ``HEIM_LIBRARY_ROOT`` at it per machine
    # (e.g. an external drive). A project (``heim.toml``) can compose additional
    # libraries in front of this one (``libraries = [".heim/library", "@shared"]``),
    # where ``@shared`` resolves to this default; outside a project this is the only
    # library. **No default**: ``None`` means "not configured", and every consumer must
    # route through :func:`heim.library.roots` / :func:`heim.library.default_root`, which
    # raise ``LibraryNotConfigured`` rather than silently falling back to a ``./data`` dir.
    library_root: Path | None = None

    # Machine-default model, used outside a project (free-play) when no model is named on the
    # CLI. In a project, ``heim.toml``'s ``[project].model`` outranks this (a project pins its
    # own model); this is the fallback so ``heim chat`` / ``serve --ui`` have a model to load
    # without a project or an explicit name. Set it with ``heim config set model <name>``.
    default_model: str | None = None

    # A running ``heim serve`` for ``heim chat`` to attach to instead of spawning its own runtime
    # per call — so the model stays loaded across invocations (no multi-second reload each time).
    # An OpenAI ``/v1`` base URL, e.g. ``http://127.0.0.1:8000``. ``heim chat --server`` overrides;
    # set a default with ``heim config set server <url>`` (or ``HEIM_CHAT_SERVER``).
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
    # library. The supervisor uses it to reap runtimes orphaned by a crashed heim (heim.supervisor).
    runtime_state_dir: Path = _default_runtime_state_dir()


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()


# Process-wide debug switch, flipped by the CLI's global ``--debug`` flag (or the
# ``HEIM_DEBUG`` env var). When on, heim prints extra diagnostics — most usefully,
# it streams the model runtime's logs instead of discarding them.
_debug = False


def set_debug(on: bool) -> None:
    """Enable or disable process-wide debug output."""
    global _debug
    _debug = on


def debug_enabled() -> bool:
    """Whether debug output is on (via ``--debug`` or ``HEIM_DEBUG``)."""
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
