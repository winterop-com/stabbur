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
import socket
import subprocess
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from kodo import chatui
from kodo.config import debug_enabled, get_settings, pinned_runtime_port
from kodo.library import LibraryModel
from kodo.models import ModelFormat, _human_size


def find_free_port() -> int:
    """Ask the OS for a free localhost TCP port (best-effort; small TOCTOU window)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# Progress/spinner goes to stderr so one-shot stdout (piped output) stays clean.
_status_console = Console(stderr=True)
_console = Console()  # stdout, for the interactive REPL frame

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


def runnable_error(model: LibraryModel) -> str | None:
    """Return why ``model`` can't be run by kodo, or ``None`` if it's runnable.

    The shared runnability check for the ``/api/load`` endpoint and locked-server
    startup (the CLI applies the same rules with richer, hint-bearing messages):
    kodo runs generative GGUF/MLX models; it rejects non-generative (embedding/
    vision) models, Ollama's native store (run those via Ollama), and safetensors
    (a convert/fine-tune source, not a runnable build).
    """
    if not model.generative:
        return f"{model.name!r} is not a chat model ({model.model_format.value}); kodo runs generative LLMs only"
    if model.is_ollama:
        return f"{model.name!r} is an Ollama model; run it via Ollama, not kodo (kodo runs GGUF/MLX)"
    if model.model_format is ModelFormat.safetensors:
        return (
            f"{model.name!r} is safetensors (a convert/fine-tune source), not directly runnable; "
            "pull a GGUF or MLX build to run it"
        )
    return None


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


def _early_exit_error(cmd: list[str], code: int | None, log_dir: Path | None, port: int) -> RuntimeError:
    """Build a RuntimeError explaining why the runtime exited during startup.

    Includes the tail of the captured runtime log (when not in --debug) and a
    port-in-use hint, so "exited before becoming ready" is actually diagnosable.
    """
    tail = ""
    if log_dir is not None:
        try:
            tail = (log_dir / f"{cmd[0]}.log").read_text(errors="replace").strip()[-2000:]
        except OSError:
            pass
    msg = f"{cmd[0]} exited before becoming ready (exit {code})"
    low = tail.lower()
    if "address already in use" in low or "couldn't bind" in low or "bind http server socket" in low:
        msg += f"; port {port} is already in use — another kodo runtime may be running"
    if tail:
        msg += f"\n--- runtime log ---\n{tail}"
    elif log_dir is None:
        msg += " — see the runtime output above"
    return RuntimeError(msg)


@contextmanager
def _serve(model: LibraryModel) -> Generator[str, None, None]:
    """Start the model's runtime server, yield its base URL, and stop it after.

    Raises:
        RuntimeError: If the runtime binary is missing or never becomes ready.
    """
    # Auto-pick a free port unless one is pinned, so concurrent kodo sessions
    # don't fight over a fixed port.
    port = pinned_runtime_port() or find_free_port()
    cmd = build_command(model, "127.0.0.1", port)
    if shutil.which(cmd[0]) is None:
        raise RuntimeError(f"{cmd[0]!r} not found on PATH. {_INSTALL_HINTS.get(cmd[0], '')}".strip())

    base = f"http://127.0.0.1:{port}"
    # In --debug, stream the runtime's logs live (inherit stderr); otherwise capture
    # them to a temp file (never DEVNULL) so an early exit can report the real cause.
    debug = debug_enabled()
    log_dir: Path | None = None
    log_fh: IO[bytes] | None = None
    stderr_target: IO[bytes] | None = None  # None → inherit (live to terminal)
    if debug:
        _status_console.print(f"[dim]runtime →[/] {' '.join(cmd)}")
    else:
        log_dir = Path(tempfile.mkdtemp(prefix="kodo-runtime-"))
        log_fh = (log_dir / f"{cmd[0]}.log").open("wb")
        stderr_target = log_fh

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stderr_target)
    try:
        size = f" ({_human_size(model.size_bytes)})" if model.size_bytes else ""
        # Spinner + elapsed time while the runtime loads the weights (seconds to
        # minutes for big models). Readiness is binary (poll /v1/models), so there's
        # no honest percentage to show — elapsed time is the truthful signal. Non-TTY
        # (piped) output degrades quietly; it's on stderr anyway.
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=_status_console,
            transient=True,
        ) as progress:
            progress.add_task(f"Loading {model.name}{size} …", total=None)
            deadline = time.time() + get_settings().runtime_load_timeout
            while time.time() < deadline:
                if proc.poll() is not None:
                    raise _early_exit_error(cmd, proc.returncode, log_dir, port)
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
        if log_fh is not None:
            log_fh.close()
        if log_dir is not None:
            shutil.rmtree(log_dir, ignore_errors=True)


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


def chat_repl(
    model: LibraryModel, max_tokens: int | None = None, system_prompt: str = "", render: bool = False
) -> None:
    """Interactive terminal chat — a clean streaming REPL over the model's /v1.

    ``render`` buffers each reply and prints it as Markdown when done, instead of
    streaming tokens live.
    """
    try:
        import readline  # up-arrow recall + line editing for input()

        readline.set_history_length(1000)
    except ImportError:
        pass
    history: list[dict[str, str]] = [{"role": "system", "content": system_prompt}] if system_prompt else []
    with _serve(model) as base:
        chatui.header(_console, model=model.name, model_format=model.model_format.value, tools=[], server=base)
        while True:
            try:
                user = input(chatui.USER_PROMPT).strip()
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
            # Spinner until the first token (prefill latency otherwise looks dead).
            # In render mode it keeps spinning through the whole reply, which is
            # then printed as Markdown in one go.
            reply = ""
            first = True
            status = chatui.thinking(_status_console)
            status.start()
            with httpx.stream("POST", f"{base}/v1/chat/completions", json=body, timeout=600) as r:
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    if payload == "[DONE]":
                        break
                    content = json.loads(payload)["choices"][0]["delta"].get("content")
                    if content:
                        reply += content
                        if not render:
                            if first:
                                status.stop()
                                chatui.assistant_prefix(_console, inline=True)
                                first = False
                            print(content, end="", flush=True)
            status.stop()
            if render:
                chatui.render_reply(_console, reply)
            else:
                print("\n", flush=True)
            history.append({"role": "assistant", "content": reply})
