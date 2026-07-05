"""A Docker-sandboxed code executor, shared by the benchmark and the exec MCP server.

Runs a snippet in a throwaway container with **no network**, a read-only rootfs (source mounted
read-only at ``/work``, an exec-able tmpfs at ``/tmp``), capped memory/CPU/pids, and a wall-clock
timeout. Language-agnostic: add a language with one entry in :data:`RUNTIMES` (image + how to
build/run). No MCP, no kodo deps.
"""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from pathlib import Path
from time import monotonic

from pydantic import BaseModel, ConfigDict


class Runtime(BaseModel):
    """How to execute a language: its image and the container argv that runs the source."""

    model_config = ConfigDict(frozen=True)

    image: str
    filename: str  # where the candidate source is written inside /work
    command: list[str]  # container argv; trailing args (test argv) are appended


# Rust compiles to a tmpfs (rootfs is read-only) then execs, forwarding any test argv via
# "$@". Python runs the source directly. Both read the source from a read-only /work mount.
RUNTIMES: dict[str, Runtime] = {
    "python": Runtime(image="python:3.13-slim", filename="main.py", command=["python", "/work/main.py"]),
    "rust": Runtime(
        image="rust:1-slim",
        filename="main.rs",
        command=["sh", "-c", 'rustc -O /work/main.rs -o /tmp/prog && exec /tmp/prog "$@"', "_"],
    ),
}

SUPPORTED_LANGUAGES = tuple(RUNTIMES)


class RunResult(BaseModel):
    """The raw outcome of executing a program once."""

    model_config = ConfigDict(frozen=True)

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_s: float


class DockerError(RuntimeError):
    """Docker is unavailable or failed to start a container."""


def docker_available() -> bool:
    """Whether a working Docker daemon is reachable."""
    try:
        subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def run_code(
    language: str,
    code: str,
    stdin: str = "",
    args: list[str] | None = None,
    timeout_s: float = 10.0,
    memory: str = "512m",
    cpus: str = "1.0",
) -> RunResult:
    """Run ``code`` in a locked-down throwaway container and capture its output.

    The container has no network, a read-only rootfs (source mounted read-only at
    ``/work``, an exec-able tmpfs at ``/tmp``), capped memory/CPU/pids, and is killed at
    ``timeout_s``. Returns the program's stdout/stderr, exit code, and whether it timed out.
    """
    runtime = RUNTIMES.get(language)
    if runtime is None:
        raise ValueError(f"unsupported language {language!r}; use one of {', '.join(SUPPORTED_LANGUAGES)}")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / runtime.filename).write_text(code)
        name = f"kodo-sandbox-{uuid.uuid4().hex[:12]}"
        cmd = [
            "docker", "run", "--rm", "-i", "--name", name,
            "--network=none", f"--memory={memory}", f"--cpus={cpus}", "--pids-limit=256",
            "--read-only", "--tmpfs", "/tmp:rw,exec,size=256m",
            "-v", f"{tmp}:/work:ro", "-w", "/work",
            runtime.image, *runtime.command, *(args or []),
        ]  # fmt: skip
        start = monotonic()
        try:
            proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)  # best-effort kill
            return RunResult(
                stdout=exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                stderr="timed out",
                exit_code=124,
                timed_out=True,
                duration_s=monotonic() - start,
            )
        except FileNotFoundError as exc:  # docker binary missing
            raise DockerError("docker not found on PATH") from exc
        return RunResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            timed_out=False,
            duration_s=monotonic() - start,
        )


__all__ = [
    "RUNTIMES",
    "SUPPORTED_LANGUAGES",
    "DockerError",
    "RunResult",
    "Runtime",
    "docker_available",
    "run_code",
]
