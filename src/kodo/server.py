"""Manage a single local model-runtime process behind a stable API.

The API process proxies ``/v1`` to whichever model is currently loaded, so the
browser SPA (and the Chrome extension) talk to one stable origin while the
underlying ``llama-server`` / ``mlx_lm.server`` process is swapped underneath.
"""

import shutil
import threading
from enum import StrEnum

import httpx

from kodo import runtime
from kodo.library import LibraryModel
from kodo.runtime import supervisor


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
        # The supervised runtime (its process group, pidfile, captured log), or None when stopped.
        self._handle: supervisor.RuntimeHandle | None = None
        self._model: LibraryModel | None = None
        self._n_ctx: int | None = None
        self._last_error: str | None = None
        # Serializes the process-mutating lifecycle ops (load/stop) at the thread
        # level. The route-layer asyncio lock can be released early if a request is
        # cancelled while its worker thread is still inside load()/stop(); this lock
        # guarantees the mutations themselves never overlap. Reentrant so load()
        # (holding it) can call stop(). Not taken by status reads, so the event loop
        # never blocks on it.
        self._lifecycle = threading.RLock()

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
        return self._handle is not None and self._handle.poll() is None

    @property
    def last_error(self) -> str | None:
        """Tail of the last runtime's stderr if it died unexpectedly, else ``None``."""
        return self._last_error

    def _read_log_tail(self, limit: int = 2000) -> str | None:
        """Return the tail of the current runtime log, if any."""
        log_path = self._handle.log_path if self._handle is not None else None
        if log_path is None:
            return None
        try:
            text = log_path.read_text(errors="replace").strip()
        except OSError:
            return None
        return text[-limit:] or None

    def _reset_proc(self) -> None:
        """Drop the runtime handle and clean up its process group + captured log."""
        if self._handle is not None:
            self._handle.stop()  # no-op kill if already dead; closes the log + removes state
            self._handle = None

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
        with self._lifecycle:
            if self._model is not None and self._model.name == model.name and self._n_ctx == n_ctx and self._alive():
                return
            self.stop()

            binary = runtime.build_command(model, self._host, self._port, n_ctx)[0]
            if shutil.which(binary) is None:
                hint = runtime._INSTALL_HINTS.get(binary, "")
                raise RuntimeError(f"{binary!r} not found on PATH. {hint}".strip())
            self._last_error = None
            # Pin the runtime to this manager's port so base_url (the proxy target) stays stable.
            self._handle = supervisor.spawn(
                lambda p: runtime.build_command(model, self._host, p, n_ctx),
                host=self._host,
                port=self._port,
                name=model.name,
            )
            self._handle.model = model
            self._model = model
            self._n_ctx = n_ctx

    def stop(self) -> None:
        """Terminate the runtime's process group if running (no zombies, no orphans)."""
        with self._lifecycle:
            # Clear the loaded-model fields first so a concurrent status read sees
            # "stopped" and doesn't try to reap the process we're already stopping.
            self._model = None
            self._n_ctx = None
            self._reset_proc()  # killpg + wait + clean up the captured log/state (no-op if stopped)
