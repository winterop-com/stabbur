"""One owner for the lifecycle of a spawned model runtime (A4).

Runtime spawning used to be implemented twice (``runtime.start`` for the CLI, ``ServerManager``
for serve) with no process-group, pidfile, or parent-death handling — so a runtime could survive
a crashed heim (orphaned, holding tens of GB), grandchildren could survive ``terminate()``, and
the auto-picked port had a TOCTOU race. This module is the single supervisor both paths use.

What it provides:

* **Process groups** — every runtime is spawned in a *new session* (``start_new_session``), so
  ``stop`` can ``killpg`` the whole group (the runtime *and* any workers it forked), not just the
  direct child.
* **Parent-death recovery** — each runtime records a ``meta.json`` (its pid/pgid/cmd + the pid of
  the heim that owns it) under ``<runtime_state_dir>/<id>/``. On a graceful exit an ``atexit``
  hook stops everything; for an *ungraceful* death (SIGKILL/OOM), the child orphans momentarily
  and is reclaimed by :func:`sweep_orphans` on the next heim start — which only kills a runtime
  whose owning heim is **gone** and whose live cmdline still matches (guarding against PID reuse).
* **Port retry** — :func:`spawn` takes a ``cmd_for_port`` factory and, on a bind collision, picks
  another port and rebuilds the command, closing the find-free-port TOCTOU (V-6).

POSIX only (macOS + Linux); heim does not serve on Windows.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from heim.config import get_settings

_META_FILENAME = "meta.json"
_LOG_FILENAME = "runtime.log"
_STOP_GRACE_S = 15.0  # SIGTERM → this long → SIGKILL
_BIND_GRACE_S = 0.5  # how long to watch a fresh spawn for an immediate port-bind failure
_PORT_RETRIES = 8

# Live handles this process owns, stopped on a graceful exit (atexit). A set keyed by identity.
_live: set[RuntimeHandle] = set()


def _runtimes_root() -> Path:
    return get_settings().runtime_state_dir


def find_free_port() -> int:
    """Ask the OS for a free localhost TCP port (best-effort; the bind race is handled by retry)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(host: str, port: int) -> bool:
    """Whether something is now accepting connections on ``host:port`` (the runtime bound it)."""
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _pid_alive(pid: int) -> bool:
    """Whether ``pid`` refers to a live process (signal 0 probes without delivering a signal)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    return True


def _process_command(pid: int) -> str:
    """The live command line for ``pid`` (``ps``), or ``""`` if it can't be read.

    ``-ww`` disables ps's column-width truncation (it defaults to the terminal width — 80 with
    no TTY), so the full argv is returned and the ``--port`` at the end of a long runtime command
    isn't dropped from the PID-reuse match. Retries on an empty result: macOS's framework-Python
    launcher (which is how the MLX runtimes run) can intermittently report a blank command to ps,
    and a spurious blank would make the PID-reuse guard skip a real orphan.
    """
    for _ in range(5):
        try:
            out = subprocess.run(
                ["ps", "-ww", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        text = out.stdout.strip()
        if text:
            return text
        time.sleep(0.05)
    return ""


def _killpg(pgid: int, sig: int) -> None:
    """Signal a whole process group, tolerating an already-dead group."""
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


class RuntimeHandle:
    """A live runtime the caller owns: hold it, poll/wait for readiness, then :meth:`stop` it.

    Mirrors the fields the old ``RuntimeProc`` exposed (``proc``/``base``/``port``) so both the CLI
    and the server can hold one. ``model`` is an opaque slot the owner sets to associate metadata.
    """

    def __init__(
        self,
        proc: subprocess.Popen[bytes],
        base: str,
        port: int,
        cmd: list[str],
        state_dir: Path,
        log_fh: IO[bytes] | None,
    ) -> None:
        self.proc = proc
        self.base = base
        self.port = port
        self.cmd = cmd
        self.state_dir = state_dir
        self.log_fh = log_fh
        self.model: Any = None  # set by the owner (a LibraryModel); supervisor treats it as opaque

    @property
    def log_path(self) -> Path | None:
        """The captured log file, if logs are being written to disk (not in --debug/stream mode)."""
        return None if self.log_fh is None else self.state_dir / _LOG_FILENAME

    def poll(self) -> int | None:
        """The runtime's exit code, or ``None`` if still running."""
        return self.proc.poll()

    def stop(self) -> None:
        """Terminate the whole process group (SIGKILL after a grace period) and clean up state."""
        if self.proc.poll() is None:
            try:
                pgid = os.getpgid(self.proc.pid)
            except ProcessLookupError:
                pgid = self.proc.pid
            _killpg(pgid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=_STOP_GRACE_S)
            except subprocess.TimeoutExpired:
                _killpg(pgid, signal.SIGKILL)
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        if self.log_fh is not None:
            self.log_fh.close()
            self.log_fh = None
        _live.discard(self)
        shutil.rmtree(self.state_dir, ignore_errors=True)


def _write_meta(state_dir: Path, proc: subprocess.Popen[bytes], cmd: list[str], port: int, name: str) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        pgid = proc.pid
    meta = {
        "owner_pid": os.getpid(),  # the heim that spawned this; a dead owner marks an orphan
        "pid": proc.pid,
        "pgid": pgid,
        "cmd": cmd,
        "port": port,
        "name": name,
        "started_at": time.time(),
    }
    (state_dir / _META_FILENAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def spawn(
    cmd_for_port: Callable[[int], list[str]],
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    stream_logs: bool = False,
    name: str = "",
) -> RuntimeHandle:
    """Spawn a runtime in its own session, record its meta, and retry on a port collision.

    Args:
        cmd_for_port: Builds the runtime argv for a chosen port (rebuilt each retry).
        host: Interface the runtime binds (readiness/collision checks connect here).
        port: A fixed port to use (no retry — the caller pinned it); ``None`` auto-picks + retries.
        stream_logs: Inherit stderr (live to the terminal, e.g. --debug) instead of a log file.
        name: A label recorded in meta.json for sweeping/debugging (e.g. the model name).

    Returns:
        A :class:`RuntimeHandle`; the runtime is spawned but not yet ready (poll via wait_ready).

    Raises:
        RuntimeError: The runtime keeps failing to bind, or exits immediately for another reason.
    """
    pinned = port is not None
    root = _runtimes_root()
    last_err = ""
    for attempt in range(1 if pinned else _PORT_RETRIES):
        chosen = port if port is not None else find_free_port()
        cmd = cmd_for_port(chosen)
        state_dir = root / f"{os.getpid()}-{chosen}-{attempt}"
        state_dir.mkdir(parents=True, exist_ok=True)
        log_fh: IO[bytes] | None = None
        stderr_target: IO[bytes] | None = None  # None → inherit (stream to terminal)
        if not stream_logs:
            log_fh = (state_dir / _LOG_FILENAME).open("wb")
            stderr_target = log_fh
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stderr_target, start_new_session=True)
        except OSError:
            if log_fh is not None:
                log_fh.close()
            shutil.rmtree(state_dir, ignore_errors=True)
            raise
        _write_meta(state_dir, proc, cmd, chosen, name)
        handle = RuntimeHandle(proc, f"http://{host}:{chosen}", chosen, cmd, state_dir, log_fh)

        # Watch briefly for an immediate exit. A bind collision (the port was taken between our
        # find_free_port and the runtime's bind) exits near-instantly with an "address in use"
        # log — pick another port and retry. Any other quick exit is a real failure. Surviving
        # the window means it bound successfully (readiness is then confirmed by wait_ready). We
        # can't use "is the port open?" as the success signal: on a collision *another* process
        # is listening there, so that would false-positive.
        retry = False
        deadline = time.time() + _BIND_GRACE_S
        while time.time() < deadline:
            rc = proc.poll()
            if rc is not None:
                tail = _read_tail(handle.log_path)
                if not pinned and attempt < _PORT_RETRIES - 1 and _is_bind_error(tail):
                    last_err = tail
                    handle.stop()  # closes log, removes state_dir, drops from _live
                    retry = True
                    break
                handle.stop()
                raise RuntimeError(f"runtime exited immediately (code {rc}); see log:\n{tail or last_err}")
            time.sleep(0.05)
        if not retry:
            _live.add(handle)  # survived the grace window → bound; hand it back to the caller
            return handle
    raise RuntimeError(f"could not bind a runtime port after {_PORT_RETRIES} tries:\n{last_err}")


def _read_tail(path: Path | None, limit: int = 2000) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        return path.read_text(errors="replace")[-limit:]
    except OSError:
        return ""


def _is_bind_error(text: str) -> bool:
    low = text.lower()
    return "address already in use" in low or "bind" in low and "in use" in low


def _cmd_matches(pid: int, meta: dict[str, object]) -> bool:
    """Whether ``pid``'s live command line still matches the recorded meta (a PID-reuse guard)."""
    live = _process_command(pid)
    if not live:
        return False
    cmd = meta.get("cmd")
    binary = Path(str(cmd[0])).name if isinstance(cmd, list) and cmd else ""
    return bool(binary) and binary in live and str(meta.get("port", "")) in live


def sweep_orphans() -> list[int]:
    """Kill runtimes left behind by a crashed heim and clean their state dirs.

    Only reaps a runtime whose **owning heim is gone** (so it never touches a runtime a
    concurrently-running heim still owns) and whose live cmdline still matches its meta (so a
    reused pid is left alone). Returns the pids actually killed. Safe to call at every startup.
    """
    reaped: list[int] = []
    root = _runtimes_root()
    if not root.is_dir():
        return reaped
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            meta = json.loads((entry / _META_FILENAME).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            shutil.rmtree(entry, ignore_errors=True)  # unreadable/partial → stale
            continue
        owner_pid = int(meta.get("owner_pid", 0))
        pid = int(meta.get("pid", 0))
        pgid = int(meta.get("pgid", pid))
        if owner_pid and _pid_alive(owner_pid):
            continue  # a live heim still owns this runtime — leave it be
        if pid and _pid_alive(pid) and _cmd_matches(pid, meta):
            _killpg(pgid, signal.SIGTERM)
            time.sleep(0.3)
            if _pid_alive(pid):
                _killpg(pgid, signal.SIGKILL)
            reaped.append(pid)
        shutil.rmtree(entry, ignore_errors=True)
    return reaped


def _stop_all_live() -> None:
    """Stop every runtime this process still owns (registered via atexit)."""
    for handle in list(_live):
        handle.stop()


atexit.register(_stop_all_live)
