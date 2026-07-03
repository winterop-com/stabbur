"""Application configuration.

``kodo.toml`` in the working directory is the primary config source: it holds
both the library location (``library_root``) and the project/assistant manifest
(``[project]`` / ``[[mcp]]``, read separately by :mod:`kodo.project`). Every
value can still be overridden per machine with a ``KODO_*`` environment
variable; ``.env`` remains an optional low-priority fallback.

Precedence (high to low): CLI args, ``KODO_*`` env vars, ``kodo.toml``, ``.env``.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


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


class Settings(BaseSettings):
    """Application settings — read from ``kodo.toml`` first, then env vars.

    Top-level keys in ``kodo.toml`` (e.g. ``library_root = "/Volumes/LLM/Library"``)
    map directly to these fields; the ``[project]`` / ``[[mcp]]`` tables are
    ignored here and read by :mod:`kodo.project`. A ``KODO_*`` environment
    variable (e.g. ``KODO_LIBRARY_ROOT``) overrides the file per machine.
    """

    model_config = SettingsConfigDict(
        env_prefix="KODO_",
        env_file=".env",
        toml_file="kodo.toml",
        extra="ignore",
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
        """Order the sources: init args > env vars > kodo.toml > .env > secrets.

        ``kodo.toml`` is the primary config file, so it outranks ``.env``; real
        environment variables still win for genuine per-machine overrides.
        """
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls),
            dotenv_settings,
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

    # Cross-origin origins allowed to call the API. Default is **same-origin only**
    # (empty list → no CORS middleware): the web UI is served by this same app, so
    # it needs no CORS, and a permissive default would let any website you visit
    # drive your local models + MCP tools from the browser. Add explicit origins
    # (e.g. the Chrome-extension origin, or a dev server) to allow cross-origin use.
    cors_origins: list[str] = []

    # Internal port the model runtime (llama-server / mlx_lm.server) listens on;
    # the API proxies /v1 to it so the SPA stays single-origin. ``None`` (the
    # default) auto-picks a free port, so concurrent kodo sessions don't collide;
    # set an int to pin it.
    runtime_port: int | None = None

    # How long to wait for a runtime to become ready before giving up. Generous
    # by default — big models (15-20 GB) can take minutes to load, especially on
    # a busy machine. A crashed runtime still fails fast (its process exits).
    runtime_load_timeout: int = 600

    # The default library — a self-contained, portable model store (models + their
    # own metadata under ``.kodo/``). Point ``KODO_LIBRARY_ROOT`` at it per machine
    # (e.g. an external drive). A project (``kodo.toml``) can compose additional
    # libraries in front of this one (``libraries = [".kodo/library", "@shared"]``),
    # where ``@shared`` resolves to this default; outside a project this is the only
    # library. See :func:`kodo.library.roots`.
    library_root: Path = Path("data")

    # Source stores to scan and back up from.
    ollama_models_dir: Path = Path.home() / ".ollama" / "models"
    lmstudio_models_dir: Path = _default_lmstudio_dir()

    # Optional Hugging Face token for gated/private repos. Falls back to the
    # standard HF_TOKEN / huggingface-cli login if unset.
    hf_token: str | None = None


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
