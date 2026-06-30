"""Manage a single local model-runtime process behind a stable API.

The API process proxies ``/v1`` to whichever model is currently loaded, so the
browser SPA (and the Chrome extension) talk to one stable origin while the
underlying ``llama-server`` / ``mlx_lm.server`` process is swapped underneath.
"""

import shutil
import subprocess
from enum import StrEnum

import httpx

from local_llm import runtime
from local_llm.library import LibraryModel


class ServerState(StrEnum):
    """Lifecycle state of the managed runtime process."""

    stopped = "stopped"
    loading = "loading"
    ready = "ready"


class ServerManager:
    """Owns at most one runtime child process and tracks the loaded model."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8090) -> None:
        self._host = host
        self._port = port
        self._proc: subprocess.Popen[bytes] | None = None
        self._model: LibraryModel | None = None

    @property
    def base_url(self) -> str:
        """Base URL of the managed runtime."""
        return f"http://{self._host}:{self._port}"

    @property
    def current(self) -> LibraryModel | None:
        """The currently loaded model, or ``None``."""
        return self._model

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

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

    def load(self, model: LibraryModel) -> None:
        """Start (or swap to) the runtime for ``model``.

        Returns immediately after spawning; readiness is polled separately via
        :meth:`ready`. A no-op if the same model is already running.

        Raises:
            RuntimeError: If the runtime binary is not installed.
        """
        if self._model is not None and self._model.name == model.name and self._alive():
            return
        self.stop()

        cmd = runtime.build_command(model, self._host, self._port)
        if shutil.which(cmd[0]) is None:
            raise RuntimeError(f"{cmd[0]!r} not found on PATH")
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._model = model

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
        self._proc = None
        self._model = None
