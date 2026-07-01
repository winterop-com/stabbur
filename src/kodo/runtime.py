"""Run library models via their OpenAI-compatible server.

* **GGUF** → llama.cpp ``llama-server`` (cross-platform; also a web chat UI).
* **MLX**  → ``mlx_lm.server`` (Apple Silicon; API only).

``run`` execs the server in the foreground (for the web UI). ``chat`` and
``generate`` start the server, talk to its ``/v1``, and shut it down — so the
terminal chat is a clean kodo REPL, not the raw llama.cpp conversation UI, and
works identically for GGUF and MLX.
"""

import json
import os
import shutil
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager

import httpx

from kodo.config import get_settings
from kodo.library import LibraryModel
from kodo.models import ModelFormat

_INSTALL_HINTS = {
    "llama-server": "Install llama.cpp: `brew install llama.cpp` (macOS) or build from source.",
    "mlx_lm.server": "Install mlx-lm: `uv tool install mlx-lm` (Apple Silicon only).",
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
    if model.model_format is ModelFormat.mlx:
        return ["mlx_lm.server", "--model", str(model.load_target), "--host", host, "--port", str(port)]
    if model.model_format is ModelFormat.safetensors:
        raise ValueError(
            f"{model.name!r} is safetensors (a convert/fine-tune source), not directly runnable; "
            "pull a GGUF or MLX build to run it"
        )
    raise ValueError(f"No runtime for format {model.model_format.value!r}")


def _exec(cmd: list[str]) -> None:
    """Replace the current process with ``cmd`` (binary must exist).

    Raises:
        RuntimeError: If the binary is not on PATH.
    """
    if shutil.which(cmd[0]) is None:
        raise RuntimeError(f"{cmd[0]!r} not found on PATH. {_INSTALL_HINTS.get(cmd[0], '')}".strip())
    os.execvp(cmd[0], cmd)


def run(model: LibraryModel, host: str, port: int) -> None:
    """Exec the runtime server for ``model`` in the foreground (replaces process)."""
    _exec(build_command(model, host, port))


@contextmanager
def _serve(model: LibraryModel) -> Generator[str, None, None]:
    """Start the model's runtime server, yield its base URL, and stop it after.

    Raises:
        RuntimeError: If the runtime binary is missing or never becomes ready.
    """
    cmd = build_command(model, "127.0.0.1", get_settings().runtime_port)
    if shutil.which(cmd[0]) is None:
        raise RuntimeError(f"{cmd[0]!r} not found on PATH. {_INSTALL_HINTS.get(cmd[0], '')}".strip())

    base = f"http://127.0.0.1:{get_settings().runtime_port}"
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + get_settings().runtime_load_timeout
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
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def generate(model: LibraryModel, prompt: str, max_tokens: int | None = None, system_prompt: str = "") -> str:
    """One-shot chat completion; returns just the reply text (clean for scripting)."""
    with _serve(model) as base:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, object] = {"messages": messages}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        resp = httpx.post(f"{base}/v1/chat/completions", json=body, timeout=600)
        resp.raise_for_status()
        content: str = resp.json()["choices"][0]["message"]["content"]
        return content


def chat_repl(model: LibraryModel, max_tokens: int | None = None, system_prompt: str = "") -> None:
    """Interactive terminal chat — a clean streaming REPL over the model's /v1."""
    history: list[dict[str, str]] = [{"role": "system", "content": system_prompt}] if system_prompt else []
    with _serve(model) as base:
        print(f"chatting with {model.name} — /exit or Ctrl-D to quit\n", flush=True)
        while True:
            try:
                user = input("you › ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user:
                continue
            if user in ("/exit", "/quit", "exit", "quit"):
                break
            history.append({"role": "user", "content": user})
            body: dict[str, object] = {"messages": history, "stream": True}
            if max_tokens is not None:
                body["max_tokens"] = max_tokens
            print("kodo › ", end="", flush=True)
            reply = ""
            with httpx.stream("POST", f"{base}/v1/chat/completions", json=body, timeout=600) as r:
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    if payload == "[DONE]":
                        break
                    content = json.loads(payload)["choices"][0]["delta"].get("content")
                    if content:
                        print(content, end="", flush=True)
                        reply += content
            print("\n", flush=True)
            history.append({"role": "assistant", "content": reply})
