"""Run library models via their OpenAI-compatible server.

* **GGUF** → llama.cpp ``llama-server`` (cross-platform).
* **MLX**  → ``mlx_lm.server`` (Apple Silicon).

``_serve`` starts the server, yields its base URL, and shuts it down after;
``generate`` / the chat TUI talk to its ``/v1`` — a clean flow that works
identically for GGUF and MLX.
"""

import os
import shutil
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from heim import capabilities, host
from heim.config import debug_enabled, get_settings, pinned_runtime_port
from heim.library import LibraryModel
from heim.models import ModelFormat, _human_size
from heim.runtime import supervisor

# The serve command reuses this to pick its own API port; runtime spawning goes through the
# supervisor (which retries on a bind collision), so there's one implementation.
find_free_port = supervisor.find_free_port


# Progress/spinner goes to stderr so one-shot stdout (piped output) stays clean.
_status_console = Console(stderr=True)

# OS-tailored install hints (see heim.host) so a missing binary points macOS users
# at Homebrew and Linux users at release binaries / their package manager.
_INSTALL_HINTS = host.install_hints()


def build_command(model: LibraryModel, host: str, port: int, n_ctx: int | None = None) -> list[str]:
    """Build the OpenAI-compatible server command line for ``model``.

    ``n_ctx`` sets the context window at load time (llama.cpp ``-c``); it only
    applies to GGUF (llama-server). MLX derives its context from the model and
    ignores it.

    Raises:
        ValueError: If the model's format has no known runtime.
    """
    if model.model_format is ModelFormat.gguf:
        cmd = ["llama-server", "-m", str(model.load_target), "--host", host, "--port", str(port)]
        if n_ctx is not None:
            cmd += ["-c", str(n_ctx)]
        if model.mmproj is not None:
            cmd += ["--mmproj", str(model.mmproj)]
        return cmd
    if model.model_format is ModelFormat.mlx:
        # Multimodal (vision) MLX checkpoints wrap the LLM under ``language_model.*``
        # plus a vision tower — text-only mlx_lm can't load them (it errors on the
        # extra params and returns nothing). Route those to mlx-vlm, which handles
        # the wrapper; keep text-only MLX on the lighter mlx_lm.
        binary = "mlx_vlm.server" if capabilities.capabilities(model).vision else "mlx_lm.server"
        return [binary, "--model", str(model.load_target), "--host", host, "--port", str(port)]
    if model.model_format is ModelFormat.safetensors:
        raise ValueError(
            f"{model.name!r} is safetensors (a convert/fine-tune source), not directly runnable; "
            "pull a GGUF or MLX build to run it"
        )
    raise ValueError(f"No runtime for format {model.model_format.value!r}")


def runnable_error(model: LibraryModel) -> str | None:
    """Return why ``model`` can't be run by heim, or ``None`` if it's runnable.

    The shared runnability check for the ``/api/load`` endpoint and locked-server
    startup (the CLI applies the same rules with richer, hint-bearing messages):
    heim runs generative GGUF/MLX models; it rejects non-generative (embedding/
    vision) models, Ollama's native store (run those via Ollama), and safetensors
    (a convert/fine-tune source, not a runnable build).
    """
    if not model.generative:
        return f"{model.name!r} is not a chat model ({model.model_format.value}); heim runs generative LLMs only"
    if model.is_ollama:
        return f"{model.name!r} is an Ollama model; run it via Ollama, not heim (heim runs GGUF/MLX)"
    if model.model_format is ModelFormat.safetensors:
        return (
            f"{model.name!r} is safetensors (a convert/fine-tune source), not directly runnable; "
            "pull a GGUF or MLX build to run it"
        )
    return None


def _early_exit_error(cmd: list[str], code: int | None, log_path: Path | None, port: int) -> RuntimeError:
    """Build a RuntimeError explaining why the runtime exited during startup.

    Includes the tail of the captured runtime log (when not in --debug) and a
    port-in-use hint, so "exited before becoming ready" is actually diagnosable.
    """
    tail = ""
    if log_path is not None:
        try:
            tail = log_path.read_text(errors="replace").strip()[-2000:]
        except OSError:
            pass
    msg = f"{cmd[0]} exited before becoming ready (exit {code})"
    low = tail.lower()
    if "address already in use" in low or "couldn't bind" in low or "bind http server socket" in low:
        msg += f"; port {port} is already in use — another heim runtime may be running"
    if tail:
        msg += f"\n--- runtime log ---\n{tail}"
    elif log_path is None:
        msg += " — see the runtime output above"
    return RuntimeError(msg)


# A runtime handle is a supervised process (:class:`heim.supervisor.RuntimeHandle`) — the CLI holds
# one, drives :func:`start` + :func:`wait_ready` (spawning split from the blocking readiness poll so
# the chat TUI can switch models without the Rich spinner :func:`_serve` uses), then :func:`stop`s it.
RuntimeProc = supervisor.RuntimeHandle


def resolve_binary(name: str) -> str | None:
    """Locate a runtime executable: heim's own environment first, then PATH.

    The MLX runtimes are heim ``extras``, so `uv tool install -e ".[mlx]"` puts
    ``mlx_lm.server`` in heim's tool environment — where uv exposes only heim's *own*
    entry points, leaving the runtime off PATH. Looking beside the running interpreter
    first means installing the extra "into heim" works without polluting a global PATH
    (the same reason :func:`heim.tools._bin_dir` exists for the bundled MCP servers).

    Returns the absolute path, or ``None`` when it is nowhere to be found.
    """
    own = Path(sys.executable).parent / name
    if own.is_file() and os.access(own, os.X_OK):
        return str(own)
    return shutil.which(name)


def start(model: LibraryModel) -> RuntimeProc:
    """Spawn the model's runtime server and return a handle — does NOT wait for readiness.

    Spawning (process group, pidfile, port-retry, orphan tracking) is owned by the supervisor;
    this adds the model-specific bits: the command, the missing-binary hint, and --debug streaming.

    Raises:
        RuntimeError: If the runtime binary is missing.
    """
    binary = build_command(model, "127.0.0.1", 0)[0]
    resolved = resolve_binary(binary)
    if resolved is None:
        raise RuntimeError(f"{binary!r} not found on PATH. {_INSTALL_HINTS.get(binary, '')}".strip())
    debug = debug_enabled()
    if debug:
        _status_console.print(f"[dim]runtime →[/] {' '.join(build_command(model, '127.0.0.1', 0))}")

    def _command(port: int) -> list[str]:
        # Spawn the resolved path, not the bare name: it may live in heim's own environment
        # rather than on PATH, which the child process would not search the same way.
        cmd = build_command(model, "127.0.0.1", port)
        cmd[0] = resolved
        return cmd

    # Pinned port (if any) is honored; otherwise the supervisor auto-picks and retries on collision.
    handle = supervisor.spawn(
        _command,
        port=pinned_runtime_port(),
        stream_logs=debug,  # --debug: inherit stderr (live to terminal) instead of a log file
        name=model.name,
    )
    handle.model = model
    return handle


def wait_ready(rt: RuntimeProc, timeout: float | None = None) -> None:
    """Block until the runtime answers ``/v1/models``, or raise on early exit / timeout.

    Readiness is binary (poll ``/v1/models``), so a caller shows elapsed time, not a percentage.

    Raises:
        RuntimeError: If the process exits early or never becomes ready in time.
    """
    deadline = time.time() + (timeout if timeout is not None else get_settings().runtime_load_timeout)
    while time.time() < deadline:
        if rt.proc.poll() is not None:
            raise _early_exit_error(rt.cmd, rt.proc.returncode, rt.log_path, rt.port)
        try:
            if httpx.get(f"{rt.base}/v1/models", timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.4)
    raise RuntimeError("runtime did not become ready in time")


def stop(rt: RuntimeProc) -> None:
    """Terminate the runtime (its whole process group; SIGKILL after a grace) and clean up logs."""
    rt.stop()


def load(model: LibraryModel) -> RuntimeProc:
    """Start the runtime and wait (with a CLI load spinner) until it's ready. The caller owns stop().

    Raises:
        RuntimeError: If the runtime binary is missing or never becomes ready.
    """
    rt = start(model)
    try:
        size = f" ({_human_size(model.size_bytes)})" if model.size_bytes else ""
        # Spinner + elapsed time while the runtime loads the weights (seconds to minutes for big
        # models). Non-TTY (piped) output degrades quietly; it's on stderr anyway.
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=_status_console,
            transient=True,
        ) as progress:
            progress.add_task(f"Loading {model.name}{size} …", total=None)
            wait_ready(rt)
    except BaseException:
        stop(rt)
        raise
    return rt


@contextmanager
def _serve(model: LibraryModel) -> Generator[str, None, None]:
    """Start the model's runtime server, yield its base URL, and stop it after.

    Raises:
        RuntimeError: If the runtime binary is missing or never becomes ready.
    """
    rt = load(model)
    try:
        yield rt.base
    finally:
        stop(rt)


def generate(
    model: LibraryModel | None,
    prompt: str,
    max_tokens: int | None = None,
    system_prompt: str = "",
    images: list[str] | None = None,
    audios: list[str] | None = None,
    base_url: str | None = None,
    model_id: str | None = None,
) -> str:
    """One-shot chat completion; returns just the reply text (clean for scripting).

    ``images`` / ``audios`` are data-URL strings sent to a multimodal model. With ``base_url`` set
    (a running ``heim serve`` or any OpenAI-compatible server), attach to that server's ``/v1``
    instead of spawning a per-call runtime — the model stays loaded, so there's no reload latency.
    ``model_id`` names the remote's model when there is no library copy (``model=None`` is only
    valid with both ``base_url`` and ``model_id`` set — there's nothing local to serve).
    """
    from contextlib import AbstractContextManager, nullcontext  # noqa: PLC0415

    from heim import agent  # noqa: PLC0415 - avoid import cycle at module load

    served: AbstractContextManager[str]
    if base_url is not None:
        served = nullcontext(base_url)
    else:
        if model is None:
            raise RuntimeError("generate() without a library model needs base_url (and model_id)")
        served = _serve(model)
    with served as base:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": agent.user_content(prompt, images, audios)})
        return _chat(base, model, messages, max_tokens, model_id)


def _chat(
    base: str,
    model: LibraryModel | None,
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
    model_id: str | None = None,
) -> str:
    """POST one chat completion to an already-served ``base`` and return the reply text."""
    from heim.runtime import sampling  # noqa: PLC0415 - avoid import cycle at module load

    # mlx-vlm requires the OpenAI ``model`` field and matches it against what it loaded (the
    # launch path); a remote router selects by it; llama-server / mlx-lm ignore it.
    if model_id is None:
        assert model is not None  # generate() guarantees a model or an explicit model_id
        model_id = str(model.load_target)
    body: dict[str, object] = {"messages": messages, "model": model_id}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    # Model-recommended sampling (incl. the anti-loop repeat_penalty default); without a local
    # copy only the mild anti-loop default applies (nothing else is knowable remotely).
    rec = sampling.recommended(model) if model is not None else sampling.defaults()
    body.update(rec.model_dump(exclude_none=True))
    resp = httpx.post(f"{base}/v1/chat/completions", json=body, timeout=600)
    resp.raise_for_status()
    content: str = resp.json()["choices"][0]["message"]["content"]
    return content


def complete(base: str, model: LibraryModel, prompt: str, system: str = "", max_tokens: int | None = None) -> str:
    """One completion against an already-served ``base`` URL — no serve/teardown per call.

    For callers (e.g. a benchmark driver) that serve a model once and prompt it many times.
    """
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return _chat(base, model, messages, max_tokens)
