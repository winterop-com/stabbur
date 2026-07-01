"""Application configuration.

``kodo.toml`` in the working directory is the primary config source: it holds
both the library location (``backup_root``) and the project/assistant manifest
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

    Top-level keys in ``kodo.toml`` (e.g. ``backup_root = "/Volumes/LLM/Library"``)
    map directly to these fields; the ``[project]`` / ``[[mcp]]`` tables are
    ignored here and read by :mod:`kodo.project`. A ``KODO_*`` environment
    variable (e.g. ``KODO_BACKUP_ROOT``) overrides the file per machine.
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
    port: int = 8000

    # Serve the browser UI (single-page app) alongside the API. The built
    # frontend is expected at ``frontend_dir``; if missing, the API still runs.
    serve_ui: bool = False
    frontend_dir: Path = Path("frontend/dist")

    # Lock the server to a single model (no switching) — for the Chrome-extension
    # backend. Empty means free model switching.
    serve_model: str | None = None

    # CORS origins allowed to call the API (the Chrome extension origin goes
    # here). Default is permissive since this binds to localhost.
    cors_origins: list[str] = ["*"]

    # Internal port the model runtime (llama-server / mlx_lm.server) listens on;
    # the API proxies /v1 to it so the SPA stays single-origin.
    runtime_port: int = 8090

    # How long to wait for a runtime to become ready before giving up. Generous
    # by default — big models (15-20 GB) can take minutes to load, especially on
    # a busy machine. A crashed runtime still fails fast (its process exits).
    runtime_load_timeout: int = 600

    # The main library root — point this at the (big) external drive. The library
    # spans this PLUS the always-local ``local_root`` below, so a small model
    # kept locally still works when the external drive is unplugged.
    backup_root: Path = Path("data")

    # Always-local library root (never on an external drive). Keep a small model
    # here for offline / drive-disconnected use; `pull --local` targets it.
    local_root: Path = Path.home() / ".kodo" / "library"

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
