"""Application configuration loaded from the environment.

All paths are configurable so that moving the backup root to a mounted
cloud drive later is a single change (the ``LOCAL_LLM_BACKUP_ROOT`` env var
or ``backup_root`` field), not a refactor.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    """Application settings loaded from environment variables.

    Environment variables are prefixed with ``LOCAL_LLM_`` (e.g.
    ``LOCAL_LLM_BACKUP_ROOT=/Volumes/cloud/llm-backup``).
    """

    model_config = SettingsConfigDict(env_prefix="LOCAL_LLM_", env_file=".env")

    app_name: str = "local-llm"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000

    # Serve the browser UI (single-page app) alongside the API. The built
    # frontend is expected at ``frontend_dir``; if missing, the API still runs.
    serve_ui: bool = False
    frontend_dir: Path = Path("frontend/dist")

    # The single destination root for downloads / backups. Defaults to a
    # project-local ``data/`` directory; point this at the 5TB external drive
    # when you are ready to move and nothing else needs to change.
    backup_root: Path = Path("data")

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
