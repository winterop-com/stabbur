"""Launch a library model with the appropriate local runtime.

Servers expose an OpenAI-compatible API at ``http://<host>:<port>/v1``:

* **GGUF** → llama.cpp ``llama-server`` (cross-platform; also serves a built-in
  web chat UI at the root URL).
* **MLX**  → ``mlx_lm.server`` (Apple Silicon; API only, no web UI).

Interactive terminal chat uses ``llama-cli --conversation`` (GGUF) or
``mlx_lm.chat`` (MLX). One-shot generation (``kodo chat -p``) briefly starts the
runtime server and calls its ``/v1`` for clean, template-applied output.
"""

import os
import shutil
import subprocess
import time

import httpx

from kodo.config import get_settings
from kodo.library import LibraryModel
from kodo.models import ModelFormat

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
        cmd = ["llama-cli", "-m", str(model.load_target), "--conversation"]
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


def generate(model: LibraryModel, prompt: str, max_tokens: int | None = None) -> str:
    """Run a one-shot chat completion and return the reply text (clean output).

    Briefly starts the model's runtime server and calls its OpenAI ``/v1`` —
    this applies the chat template and yields just the message content, unlike
    ``llama-cli`` whose conversation UI pollutes stdout. Works for GGUF and MLX.

    Raises:
        RuntimeError: If the runtime binary is missing or never becomes ready.
    """
    cmd = build_command(model, "127.0.0.1", get_settings().runtime_port)
    if shutil.which(cmd[0]) is None:
        raise RuntimeError(f"{cmd[0]!r} not found on PATH. {_INSTALL_HINTS.get(cmd[0], '')}".strip())

    base = f"http://127.0.0.1:{get_settings().runtime_port}"
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 180
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("runtime exited before becoming ready")
            try:
                if httpx.get(f"{base}/v1/models", timeout=2).status_code < 500:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.4)
        else:
            raise RuntimeError("runtime did not become ready in time")

        body: dict[str, object] = {"messages": [{"role": "user", "content": prompt}]}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        resp = httpx.post(f"{base}/v1/chat/completions", json=body, timeout=600)
        resp.raise_for_status()
        content: str = resp.json()["choices"][0]["message"]["content"]
        return content
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
