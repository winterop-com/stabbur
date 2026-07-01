"""Manage a single local model-runtime process behind a stable API.

The API process proxies ``/v1`` to whichever model is currently loaded, so the
browser SPA (and the Chrome extension) talk to one stable origin while the
underlying ``llama-server`` / ``mlx_lm.server`` process is swapped underneath.
"""

import shutil
import subprocess
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import IO

import httpx

from kodo import runtime
from kodo.library import LibraryModel


class ServerState(StrEnum):
    """Lifecycle state of the managed runtime process."""

    stopped = "stopped"
    loading = "loading"
    ready = "ready"


class ServerManager:
    """Owns at most one runtime child process and tracks the loaded model."""

    def __init__(self, host: str = "127.0.0.1", port: int | None = None) -> None:
        self._host = host
        # None → auto-pick a free port (chosen once, up front, so base_url is stable
        # for the proxy); pin it by passing an explicit port.
        self._port = port if port is not None else runtime.find_free_port()
        self._proc: subprocess.Popen[bytes] | None = None
        self._model: LibraryModel | None = None
        self._n_ctx: int | None = None
        # Runtime stderr is captured to a temp log so a crash/bad-model failure is
        # diagnosable (unlike DEVNULL); the tail is retained as ``last_error``.
        self._log_dir: Path | None = None
        self._log_fh: IO[bytes] | None = None
        self._last_error: str | None = None

    @property
    def base_url(self) -> str:
        """Base URL of the managed runtime."""
        return f"http://{self._host}:{self._port}"

    @property
    def current(self) -> LibraryModel | None:
        """The currently loaded model, or ``None``.

        Reaps a runtime child that has exited (crash / OOM / killed / bad model):
        a dead process is not a loaded model, so callers (status, the ``/v1``
        proxy) must not treat the stale name as runnable.
        """
        if self._model is not None and not self._alive():
            # Died unexpectedly (crash / OOM / bad model) — keep the log tail so
            # callers can report *why* instead of a silent disappearance.
            self._last_error = self._read_log_tail() or self._last_error
            self._reset_proc()
            self._model = None
        return self._model

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def last_error(self) -> str | None:
        """Tail of the last runtime's stderr if it died unexpectedly, else ``None``."""
        return self._last_error

    def _read_log_tail(self, limit: int = 2000) -> str | None:
        """Return the tail of the current runtime log, if any."""
        if self._log_dir is None:
            return None
        try:
            text = (self._log_dir / "runtime.log").read_text(errors="replace").strip()
        except OSError:
            return None
        return text[-limit:] or None

    def _reset_proc(self) -> None:
        """Drop the process handle and clean up its captured log."""
        self._proc = None
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None
        if self._log_dir is not None:
            shutil.rmtree(self._log_dir, ignore_errors=True)
            self._log_dir = None

    async def ready(self) -> bool:
        """Whether the runtime is up and answering requests."""
        if not self._alive():
            return False
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                resp = await client.get(f"{self.base_url}/v1/models")
                return resp.status_code < 500
        except httpx.HTTPError:
            return False

    async def state(self) -> ServerState:
        """Coarse lifecycle state for the UI."""
        if not self._alive():
            return ServerState.stopped
        return ServerState.ready if await self.ready() else ServerState.loading

    @property
    def n_ctx(self) -> int | None:
        """The context window the current model was loaded with (``None`` = default)."""
        return self._n_ctx if self.current is not None else None

    def load(self, model: LibraryModel, n_ctx: int | None = None) -> None:
        """Start (or swap to) the runtime for ``model``.

        Returns immediately after spawning; readiness is polled separately via
        :meth:`ready`. A no-op if the same model *and* context are already running
        (a different ``n_ctx`` reloads, since context is fixed at load time).

        Raises:
            RuntimeError: If the runtime binary is not installed.
        """
        if self._model is not None and self._model.name == model.name and self._n_ctx == n_ctx and self._alive():
            return
        self.stop()

        cmd = runtime.build_command(model, self._host, self._port, n_ctx)
        if shutil.which(cmd[0]) is None:
            hint = runtime._INSTALL_HINTS.get(cmd[0], "")
            raise RuntimeError(f"{cmd[0]!r} not found on PATH. {hint}".strip())
        self._last_error = None
        self._log_dir = Path(tempfile.mkdtemp(prefix="kodo-runtime-"))
        self._log_fh = (self._log_dir / "runtime.log").open("wb")
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=self._log_fh)
        self._model = model
        self._n_ctx = n_ctx

    def stop(self) -> None:
        """Terminate the runtime process if running (no zombies)."""
        if self._alive():
            assert self._proc is not None
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._reset_proc()
        self._model = None
        self._n_ctx = None
