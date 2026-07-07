"""Application configuration.

``kodo.toml`` in the working directory is the primary config source: it holds
both the library location (``library_root``) and the project/assistant manifest
(``[project]`` / ``[voice]``, read separately by :mod:`kodo.project`; tools live in
``.mcp.json``, see :mod:`kodo.mcpservers`). Every
value can still be overridden per machine with a ``KODO_*`` environment
variable; ``.env`` remains an optional low-priority fallback. Below that sits the
durable **machine config** (:mod:`kodo.userconfig`, ``~/.config/kodo/config.toml``),
written by ``kodo config`` / ``kodo setup`` — the persistent per-machine default
(library location, default model) so a fresh box needs no shell export.

Precedence (high to low): CLI args, ``KODO_*`` env vars, ``kodo.toml``, ``.env``,
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


class _KodoTomlSource(PydanticBaseSettingsSource):
    """Feed machine settings from the shared ``kodo.toml`` parse (:func:`kodo.project.read_raw`).

    ``kodo.toml`` holds both machine settings (these) and the portable project manifest
    (``[project]`` etc., read by :mod:`kodo.project`). Routing this source through the same
    ``read_raw`` parser the manifest uses means the file is parsed by **one** code path, so a
    malformed file raises a single, clean ``ProjectError`` rather than crashing differently in
    each reader (A1). Top-level scalar/list keys map to :class:`Settings` fields; the manifest
    tables (``project``/``mcp``/``voice``/``libraries``) are ignored here (``extra="ignore"``).
    """

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: ARG002
        return None, field_name, False  # unused: __call__ returns the whole dict below

    def __call__(self) -> dict[str, Any]:
        from kodo import project  # noqa: PLC0415 - lazy: avoid an import cycle at module load

        return project.read_raw()


class _MachineConfigSource(PydanticBaseSettingsSource):
    """Feed settings from the durable machine config (:func:`kodo.userconfig.read`).

    The lowest-priority real source: per-machine defaults (library location, default model)
    written by ``kodo config`` / ``kodo setup``. Its TOML keys are already :class:`Settings`
    field names, so the parsed dict maps directly; unknown keys are ignored (``extra="ignore"``).
    """

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: ARG002
        return None, field_name, False  # unused: __call__ returns the whole dict below

    def __call__(self) -> dict[str, Any]:
        from kodo import userconfig  # noqa: PLC0415 - lazy, symmetry with _KodoTomlSource

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
    travel with the drive), and no longer ``~/.kodo`` (which wasn't XDG-compliant).
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "kodo" / "runtimes"
    cache = os.environ.get("XDG_CACHE_HOME")
    return (Path(cache) if cache else Path.home() / ".cache") / "kodo" / "runtimes"


class Settings(BaseSettings):
    """Application settings — read from ``kodo.toml`` first, then env vars.

    Top-level keys in ``kodo.toml`` (e.g. ``library_root = "/path/to/your/library"``)
    map directly to these fields; the ``[project]`` / ``[voice]`` tables are
    ignored here and read by :mod:`kodo.project`. A ``KODO_*`` environment
    variable (e.g. ``KODO_LIBRARY_ROOT``) overrides the file per machine.
    """

    model_config = SettingsConfigDict(
        env_prefix="KODO_",
        env_file=".env",
        extra="ignore",  # manifest tables ([project]/[voice]/…) coexist in kodo.toml; ignore them here
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
        """Order the sources: init args > env vars > kodo.toml > .env > machine config > secrets.

        ``kodo.toml`` is the primary config file, so it outranks ``.env``; real
        environment variables still win for genuine per-machine overrides. The machine
        config (:mod:`kodo.userconfig`) sits at the bottom as the durable default a project
        or env var can override.
        """
        return (
            init_settings,
            env_settings,
            _KodoTomlSource(settings_cls),
            dotenv_settings,
            _MachineConfigSource(settings_cls),
            file_secret_settings,
        )

    app_name: str = "kodo"
    debug: bool = False
    host: str = "127.0.0.1"
    # Web server (kodo serve) port. ``None`` (default) auto-picks a free port and
    # prints the URL on startup; set an int (or pass --port) to pin it for a stable
    # bookmark / Chrome-extension origin.
    port: int | None = None

    # Serve the browser UI (single-page app) alongside the API. Defaults to the
    # ``frontend/dist`` that ships with the source tree (resolved from this file, not the
    # CWD) so ``serve --ui`` works from any directory — e.g. a globally-installed kodo run
    # inside a project. If it's missing (not built), the API still runs.
    serve_ui: bool = False
    frontend_dir: Path = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

    # Lock the server to a single model (no switching) — for the Chrome-extension
    # backend. Empty means free model switching.
    serve_model: str | None = None

    # Bearer token required on the API (``/api``, ``/v1``, ``/models``) when set. Empty (the
    # default) disables auth — safe for the loopback-only default bind. ``kodo serve`` auto-fills
    # a random one when it binds a non-loopback address, so exposing the server to the LAN never
    # leaves model control + tool execution unauthenticated (V-14). Clients send it as
    # ``Authorization: Bearer <token>``; the SPA also accepts it via a ``?token=`` URL param.
    auth_token: str = ""

    # Cross-origin origins allowed to call the API. Default is **same-origin only**
    # (empty list → no CORS middleware): the web UI is served by this same app, so
    # it needs no CORS, and a permissive default would let any website you visit
    # drive your local models + MCP tools from the browser. Add explicit origins
    # (e.g. the Chrome-extension origin, or a dev server) to allow cross-origin use.
    # ``NoDecode`` + the validator below accept a plain ``KODO_CORS_ORIGINS=a,b`` (or a single
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
    # default) auto-picks a free port, so concurrent kodo sessions don't collide;
    # set an int to pin it.
    runtime_port: int | None = None

    # How long to wait for a runtime to become ready before giving up. Generous
    # by default — big models (15-20 GB) can take minutes to load, especially on
    # a busy machine. A crashed runtime still fails fast (its process exits).
    runtime_load_timeout: int = 600

    # Max seconds for a single MCP tool call in the agent loop before it's abandoned
    # (the model gets an error back and the loop continues). Bounds a wedged tool or
    # server — e.g. one shelling out to a command that never returns — so it can't stall
    # a chat or benchmark indefinitely. 0 disables the bound (wait forever).
    tool_timeout: float = 120.0

    # The default library — a self-contained, portable model store (models + their
    # own metadata under ``.kodo/``). Point ``KODO_LIBRARY_ROOT`` at it per machine
    # (e.g. an external drive). A project (``kodo.toml``) can compose additional
    # libraries in front of this one (``libraries = [".kodo/library", "@shared"]``),
    # where ``@shared`` resolves to this default; outside a project this is the only
    # library. **No default**: ``None`` means "not configured", and every consumer must
    # route through :func:`kodo.library.roots` / :func:`kodo.library.default_root`, which
    # raise ``LibraryNotConfigured`` rather than silently falling back to a ``./data`` dir.
    library_root: Path | None = None

    # Machine-default model, used outside a project (free-play) when no model is named on the
    # CLI. In a project, ``kodo.toml``'s ``[project].model`` outranks this (a project pins its
    # own model); this is the fallback so ``kodo chat`` / ``serve --ui`` have a model to load
    # without a project or an explicit name. Set it with ``kodo config set model <name>``.
    default_model: str | None = None

    # A running ``kodo serve`` for ``kodo chat`` to attach to instead of spawning its own runtime
    # per call — so the model stays loaded across invocations (no multi-second reload each time).
    # An OpenAI ``/v1`` base URL, e.g. ``http://127.0.0.1:8000``. ``kodo chat --server`` overrides;
    # set a default with ``kodo config set server <url>`` (or ``KODO_CHAT_SERVER``).
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
    # library. The supervisor uses it to reap runtimes orphaned by a crashed kodo (kodo.supervisor).
    runtime_state_dir: Path = _default_runtime_state_dir()


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()


# Process-wide debug switch, flipped by the CLI's global ``--debug`` flag (or the
# ``KODO_DEBUG`` env var). When on, kodo prints extra diagnostics — most usefully,
# it streams the model runtime's logs instead of discarding them.
_debug = False


def set_debug(on: bool) -> None:
    """Enable or disable process-wide debug output."""
    global _debug
    _debug = on


def debug_enabled() -> bool:
    """Whether debug output is on (via ``--debug`` or ``KODO_DEBUG``)."""
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
