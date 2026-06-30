"""Launch a library model with the appropriate local runtime.

Servers expose an OpenAI-compatible API at ``http://<host>:<port>/v1``:

* **GGUF** → llama.cpp ``llama-server`` (cross-platform; also serves a built-in
  web chat UI at the root URL).
* **MLX**  → ``mlx_lm.server`` (Apple Silicon; API only, no web UI).

Interactive terminal chat uses ``llama-cli -cnv`` (GGUF) or ``mlx_lm.chat`` (MLX).
"""

import os
import shutil

from local_llm.library import LibraryModel
from local_llm.models import ModelFormat

_INSTALL_HINTS = {
    "llama-server": "Install llama.cpp: `brew install llama.cpp` (macOS) or build from source.",
    "llama-cli": "Install llama.cpp: `brew install llama.cpp` (macOS) or build from source.",
    "mlx_lm.server": "Install mlx-lm: `uv tool install mlx-lm` (Apple Silicon only).",
    "mlx_lm.chat": "Install mlx-lm: `uv tool install mlx-lm` (Apple Silicon only).",
}


def serves_web_ui(model: LibraryModel) -> bool:
    """Whether this model's server provides a built-in web chat UI."""
    return model.model_format is ModelFormat.gguf  # llama-server ships one


def build_command(model: LibraryModel, host: str, port: int) -> list[str]:
    """Build the OpenAI-compatible server command line for ``model``.

    Raises:
        ValueError: If the model's format has no known runtime.
    """
    if model.model_format is ModelFormat.gguf:
        cmd = ["llama-server", "-m", str(model.load_target), "--host", host, "--port", str(port)]
        if model.mmproj is not None:
            cmd += ["--mmproj", str(model.mmproj)]
        return cmd
    if model.model_format in (ModelFormat.mlx, ModelFormat.safetensors):
        return ["mlx_lm.server", "--model", str(model.load_target), "--host", host, "--port", str(port)]
    raise ValueError(f"No runtime for format {model.model_format.value!r}")


def build_chat_command(model: LibraryModel) -> list[str]:
    """Build the interactive terminal-chat command line for ``model``.

    Raises:
        ValueError: If the model's format has no known runtime.
    """
    if model.model_format is ModelFormat.gguf:
        cmd = ["llama-cli", "-m", str(model.load_target), "-cnv"]
        if model.mmproj is not None:
            cmd += ["--mmproj", str(model.mmproj)]
        return cmd
    if model.model_format in (ModelFormat.mlx, ModelFormat.safetensors):
        return ["mlx_lm.chat", "--model", str(model.load_target)]
    raise ValueError(f"No runtime for format {model.model_format.value!r}")


def _exec(cmd: list[str]) -> None:
    """Replace the current process with ``cmd`` after checking the binary exists.

    Raises:
        RuntimeError: If the binary is not on PATH.
    """
    binary = cmd[0]
    if shutil.which(binary) is None:
        hint = _INSTALL_HINTS.get(binary, "")
        raise RuntimeError(f"{binary!r} not found on PATH. {hint}".strip())
    os.execvp(binary, cmd)


def run(model: LibraryModel, host: str, port: int) -> None:
    """Exec the runtime server for ``model`` (replaces the current process)."""
    _exec(build_command(model, host, port))


def chat(model: LibraryModel) -> None:
    """Exec an interactive terminal chat for ``model`` (replaces the process)."""
    _exec(build_chat_command(model))
