"""Install a canonical library model into an external runtime (a *consumer*).

The library holds **one canonical copy per (model, format)** — a loose GGUF/MLX
tree on the drive. Some runtimes can't run a loose file in place: Ollama keeps a
content-addressed blob store and needs the GGUF *imported* (``ollama create``).
This module feeds those runtimes **from** the canonical library, so the library
stays the single source of truth and the runtime copy is regenerable.

Currently supports Ollama; LM Studio (which reads loose GGUF/MLX directly, so it
only needs a pointer, not a copy) is the next consumer.
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

    modelfile: str
    """The generated Modelfile (Ollama), for display/debugging."""


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
        escaped = system.replace('"', '\\"')
        lines.append(f'SYSTEM "{escaped}"')
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
        mf_path.write_text(modelfile)
        proc = subprocess.run(
            ["ollama", "create", target, "-f", str(mf_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"`ollama create {target}` failed: {detail}")
    return InstallResult(runtime="ollama", name=target, modelfile=modelfile)
