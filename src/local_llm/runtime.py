"""Launch a library model with the appropriate local runtime.

Both runtimes expose an OpenAI-compatible API at ``http://<host>:<port>/v1``:

* **GGUF** → llama.cpp ``llama-server`` (cross-platform).
* **MLX**  → ``mlx_lm.server`` (Apple Silicon).
"""

import os
import shutil

from local_llm.library import LibraryModel
from local_llm.models import ModelFormat

_INSTALL_HINTS = {
    "llama-server": "Install llama.cpp: `brew install llama.cpp` (macOS) or build from source.",
    "mlx_lm.server": "Install mlx-lm: `uv tool install mlx-lm` (Apple Silicon only).",
}


def _gguf_weights(model: LibraryModel) -> tuple[str, str | None]:
    """Return ``(main_gguf, mmproj_gguf|None)`` for a GGUF model directory.

    Picks the first shard for sharded models, else the largest file; any
    ``mmproj-*`` projector is returned separately for multimodal models.
    """
    ggufs = sorted(model.path.glob("*.gguf"))
    if not ggufs:
        raise FileNotFoundError(f"No .gguf files in {model.path}")
    mmproj = next((g for g in ggufs if g.name.lower().startswith("mmproj")), None)
    weights = [g for g in ggufs if g != mmproj]
    shard = next((g for g in weights if "00001-of-" in g.name), None)
    main = shard or max(weights, key=lambda p: p.stat().st_size)
    return str(main), (str(mmproj) if mmproj else None)


def build_command(model: LibraryModel, host: str, port: int) -> list[str]:
    """Build the server command line for ``model``.

    Args:
        model: The library model to serve.
        host: Bind address.
        port: Bind port.

    Returns:
        The argv list to exec.

    Raises:
        ValueError: If the model's format has no known runtime.
    """
    if model.model_format is ModelFormat.gguf:
        main, mmproj = _gguf_weights(model)
        cmd = ["llama-server", "-m", main, "--host", host, "--port", str(port)]
        if mmproj is not None:
            cmd += ["--mmproj", mmproj]
        return cmd
    if model.model_format in (ModelFormat.mlx, ModelFormat.safetensors):
        return ["mlx_lm.server", "--model", str(model.path), "--host", host, "--port", str(port)]
    raise ValueError(f"No runtime for format {model.model_format.value!r}")


def run(model: LibraryModel, host: str, port: int) -> None:
    """Exec the runtime server for ``model`` (replaces the current process).

    Args:
        model: The library model to serve.
        host: Bind address.
        port: Bind port.

    Raises:
        RuntimeError: If the required runtime binary is not installed.
    """
    cmd = build_command(model, host, port)
    binary = cmd[0]
    if shutil.which(binary) is None:
        hint = _INSTALL_HINTS.get(binary, "")
        raise RuntimeError(f"{binary!r} not found on PATH. {hint}".strip())
    os.execvp(binary, cmd)
