"""Install a canonical library model into an external runtime (a *consumer*).

The library holds **one canonical copy per (model, format)** — a loose GGUF/MLX
tree on the drive. This module feeds external runtimes **from** that copy, so the
library stays the single source of truth:

* **Ollama** keeps a content-addressed blob store and can't run a loose file, so the
  GGUF is *imported* (``ollama create``) — a regenerable copy.
* **LM Studio** reads loose GGUF/MLX directly, and the library's ``gguf/<publisher>/<repo>/``
  and ``mlx/<publisher>/<repo>/`` buckets already match LM Studio's expected layout — so it
  just needs a *pointer*: a symlink from LM Studio's models dir into the library copy (the link
  lives on the machine disk, so the drive's no-symlink limitation doesn't apply). Zero copy.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from kodo.library import LibraryModel
from kodo.models import ModelFormat

# Ollama model names are lowercase; the tag defaults to ``latest``. Anything outside
# ``[a-z0-9._-]`` is collapsed to a single ``-`` so a repo tail becomes a valid name.
_OLLAMA_NAME_STRIP = re.compile(r"[^a-z0-9._-]+")


class InstallResult(BaseModel):
    """Outcome of installing a library model into a runtime."""

    model_config = ConfigDict(frozen=True)

    runtime: str
    name: str
    """The name the model is registered under in the target runtime."""

    detail: str = ""
    """Runtime-specific detail for display/debugging (Ollama Modelfile, LM Studio link path)."""


def ollama_available() -> bool:
    """Whether the ``ollama`` binary is on ``PATH``."""
    return shutil.which("ollama") is not None


def ollama_daemon_up() -> bool:
    """Whether a local Ollama daemon is reachable (``ollama create`` talks to it)."""
    if not ollama_available():
        return False
    try:
        proc = subprocess.run(["ollama", "ps"], capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def ollama_name(model_name: str) -> str:
    """Suggest a valid Ollama name from a library model name.

    Uses the repo tail with a trailing ``-GGUF`` dropped, lowercased and sanitized —
    e.g. ``unsloth/Qwen3.5-4B-GGUF`` -> ``qwen3.5-4b``.
    """
    tail = model_name.rsplit("/", 1)[-1]
    tail = re.sub(r"-gguf$", "", tail, flags=re.IGNORECASE)
    name = _OLLAMA_NAME_STRIP.sub("-", tail.lower()).strip("-")
    return name or "model"


def build_modelfile(model: LibraryModel, system: str | None = None) -> str:
    """Build an Ollama Modelfile that imports the model's canonical GGUF.

    ``FROM <gguf>`` is enough for Ollama to read the chat template and stop tokens
    from the GGUF metadata; an optional ``SYSTEM`` sets a default system prompt.
    """
    lines = [f"FROM {model.load_target}"]
    if model.mmproj is not None:
        # A vision GGUF pairs the projector; Ollama takes it as a second FROM.
        lines.append(f"FROM {model.mmproj}")
    if system:
        if "\n" in system:  # Ollama needs a triple-quoted block for a multi-line SYSTEM prompt
            lines.append(f'SYSTEM """{system}"""')
        else:
            lines.append(f'SYSTEM "{system.replace(chr(34), chr(92) + chr(34))}"')
    return "\n".join(lines) + "\n"


def install_ollama(model: LibraryModel, *, name: str | None = None, system: str | None = None) -> InstallResult:
    """Import a canonical library GGUF into a running Ollama (``ollama create``).

    Args:
        model: A GGUF library model (the canonical copy stays on the drive).
        name: Ollama name to register under; defaults to a sanitized repo tail.
        system: Optional default system prompt baked into the Modelfile.

    Returns:
        The install result (runtime, registered name, generated Modelfile).

    Raises:
        RuntimeError: Ollama isn't installed, its daemon is down, the model isn't
            GGUF, or ``ollama create`` fails.
    """
    if model.model_format is not ModelFormat.gguf:
        raise RuntimeError(
            f"{model.name!r} is {model.model_format.value}; Ollama imports GGUF only "
            "(MLX/safetensors aren't supported by Ollama)."
        )
    if not ollama_available():
        raise RuntimeError("Ollama is not installed — get it from https://ollama.com (or `brew install ollama`).")
    if not ollama_daemon_up():
        raise RuntimeError("Ollama daemon is not running — start it with `ollama serve` (or launch the Ollama app).")

    target = name or ollama_name(model.name)
    modelfile = build_modelfile(model, system=system)
    with tempfile.TemporaryDirectory() as tmp:
        mf_path = Path(tmp) / "Modelfile"
        mf_path.write_text(modelfile, encoding="utf-8")
        proc = subprocess.run(
            ["ollama", "create", target, "-f", str(mf_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"`ollama create {target}` failed: {err}")
    return InstallResult(runtime="ollama", name=target, detail=modelfile)


def lmstudio_models_dir() -> Path:
    """LM Studio's models directory (``KODO_LMSTUDIO_MODELS_DIR`` / the detected default)."""
    from kodo.config import get_settings  # noqa: PLC0415 - avoid an import cycle at module load

    return get_settings().lmstudio_models_dir


def install_lmstudio(model: LibraryModel) -> InstallResult:
    """Point LM Studio at a canonical library GGUF/MLX model (a zero-copy symlink).

    Links ``<lmstudio_models_dir>/<publisher>/<repo>`` to the library's copy, which already
    has LM Studio's ``<publisher>/<repo>/<file>`` shape. Idempotent; refuses to clobber a real
    directory LM Studio downloaded itself.

    Raises:
        RuntimeError: the model isn't a loose GGUF/MLX (Ollama's store / safetensors don't apply),
            a real (non-symlink) model dir already occupies the target, or the symlink fails.
    """
    if model.is_ollama or model.model_format not in (ModelFormat.gguf, ModelFormat.mlx):
        raise RuntimeError(
            f"LM Studio reads loose GGUF/MLX; {model.name!r} is "
            f"{'an Ollama model' if model.is_ollama else model.model_format.value} — nothing to link."
        )
    base = lmstudio_models_dir()
    link = base / model.name  # <base>/<publisher>/<repo>
    target = model.path.resolve()

    if link.is_symlink():
        if link.resolve() == target:
            return InstallResult(runtime="lmstudio", name=model.name, detail=f"already linked → {target}")
        link.unlink()  # a stale link (moved library) — replace it
    elif link.exists():
        raise RuntimeError(
            f"{link} already exists and isn't a symlink (LM Studio has its own copy); "
            "remove it first to point at the library copy."
        )

    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        raise RuntimeError(f"couldn't symlink into LM Studio ({link}): {exc}") from exc
    return InstallResult(runtime="lmstudio", name=model.name, detail=f"{link} -> {target}")
