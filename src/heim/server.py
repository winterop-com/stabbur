"""Manage the model backend behind a stable API: a local runtime process, or a remote /v1.

The API process proxies ``/v1`` to whichever model is currently loaded, so the
browser SPA (and the Chrome extension) talk to one stable origin while the
underlying ``llama-server`` / ``mlx_lm.server`` process is swapped underneath.
:class:`UpstreamManager` is the remote counterpart (``serve --upstream``): same
read surface, but "loading" a model means selecting one of the remote's ids.
"""

import shutil
import threading
import time
from enum import StrEnum

import httpx
from pydantic import BaseModel, ConfigDict

from heim import runtime
from heim.library import LibraryModel
from heim.runtime import supervisor


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
        # (holding it) can call stop(). Status reads only try-acquire it (never
        # block), so the event loop never stalls on it.
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
        # Snapshot both fields: reads run on the event loop while load()/stop() mutate
        # them from worker threads, so self._handle must never be dereferenced twice.
        model, handle = self._model, self._handle
        if model is None:
            return None
        if handle is not None and handle.poll() is None:
            return model
        # The runtime looks dead. Reap it only if no lifecycle op is mid-flight —
        # blocking on the lock here would stall the event loop behind a load()/stop(),
        # and a mid-swap snapshot isn't proof of death anyway. The in-flight op leaves
        # consistent state itself; a genuinely dead runtime is reaped by the next read.
        if self._lifecycle.acquire(blocking=False):
            try:
                if self._model is not None and not self._alive():
                    # Died unexpectedly — keep the log tail so callers can report
                    # *why* instead of a silent disappearance.
                    self._last_error = self._read_log_tail() or self._last_error
                    self._reset_proc()
                    self._model = None
                return self._model
            finally:
                self._lifecycle.release()
        return None  # a load/stop is mid-flight: transitional, report "not loaded"

    def _alive(self) -> bool:
        handle = self._handle  # snapshot: a concurrent stop() may null the field mid-check
        return handle is not None and handle.poll() is None

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


class UpstreamModel(BaseModel):
    """A model the upstream server lists on its ``/v1/models``."""

    model_config = ConfigDict(frozen=True)

    name: str  # the remote's model id (parity with LibraryModel.name for the routers)
    loaded: bool = False  # llama-server router mode reports per-model state; others read as False
    vision: bool = False  # from the listing's input_modalities, when reported
    audio: bool = False


class UpstreamManager:
    """Front a remote OpenAI-compatible ``/v1`` instead of spawning local runtimes.

    The ``serve --upstream`` backend: heim's agent loop, MCP tools, confirm gate, and the
    web UI run here while the models run on the remote box (a llama-server in router mode,
    LM Studio, mlx-lm, another heim serve). Duck-types :class:`ServerManager`'s read surface
    (``current``/``state``/``ready``/``n_ctx``/``base_url``/``last_error``/``stop``) so the
    serving routers can hold either. "Loading" a model is selecting one of the remote's ids —
    a router-mode server hot-swaps on the next request, so no process is ever spawned or
    stopped here, and ``stop`` merely clears the selection (nothing is unloaded remotely).
    """

    # Probe pacing: a successful probe is trusted for _READY_TTL seconds (no re-probe), and a
    # FAILED probe within _READY_GRACE of the last success still reports ready. Status is polled
    # every few seconds by the UI; without keep-alive + this hysteresis, every poll opens a fresh
    # connection (DNS + TCP) to the upstream and a single slow/lost probe flaps the UI to
    # "disconnected" even though generations are streaming fine.
    _READY_TTL = 10.0
    _READY_GRACE = 30.0
    # A busy llama-server answers /v1/models slowly (measured 1-3s while generating, and
    # occasionally worse), so give the listing real room rather than reporting a false outage.
    _LISTING_TIMEOUT = 15.0

    def __init__(self, upstream: str) -> None:
        # Accept with or without a trailing /v1 — routes append their own /v1 paths.
        self._upstream = upstream.strip().rstrip("/").removesuffix("/v1").rstrip("/")
        self._selected: UpstreamModel | None = None
        self._last_error: str | None = None
        self._lock = threading.Lock()  # serializes selection writes (routes mutate off-loop)
        self._ready_at = 0.0  # time.monotonic() of the last successful probe (0 = never)
        self._http: httpx.AsyncClient | None = None  # persistent keep-alive client for probes
        self._models_at = 0.0  # time.monotonic() of the cached listing
        self._models: list[UpstreamModel] = []

    @property
    def base_url(self) -> str:
        """Base URL of the upstream server (no trailing ``/v1``)."""
        return self._upstream

    @property
    def current(self) -> UpstreamModel | None:
        """The selected remote model, or ``None``."""
        return self._selected

    @property
    def last_error(self) -> str | None:
        """Why the upstream looked unhealthy on the last probe, else ``None``."""
        return self._last_error

    @property
    def n_ctx(self) -> int | None:
        """Context window — decided by the remote's own presets, unknown here."""
        return None

    def models(self) -> list[UpstreamModel]:
        """The upstream's ``GET /v1/models`` as :class:`UpstreamModel` rows.

        ``loaded`` comes from llama-server router mode's per-model ``status``; the
        vision/audio flags from ``architecture.input_modalities`` when reported.

        Raises:
            RuntimeError: If the upstream is unreachable or answers garbage.
        """
        now = time.monotonic()
        if self._models and now - self._models_at < 5.0:
            return list(self._models)  # fresh enough; don't re-hit the upstream per poll
        try:
            resp = httpx.get(f"{self._upstream}/v1/models", timeout=self._LISTING_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"upstream {self._upstream} unreachable: {exc}") from exc
        rows = data.get("data") if isinstance(data, dict) else None
        out: list[UpstreamModel] = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    continue
                status = row.get("status")
                arch = row.get("architecture")
                modalities = arch.get("input_modalities") if isinstance(arch, dict) else None
                mods = [m for m in modalities if isinstance(m, str)] if isinstance(modalities, list) else []
                out.append(
                    UpstreamModel(
                        name=row["id"],
                        loaded=isinstance(status, dict) and status.get("value") == "loaded",
                        vision="image" in mods,
                        audio="audio" in mods,
                    )
                )
        self._models, self._models_at = out, now
        return list(out)

    def load_by_name(self, name: str) -> None:
        """Select the remote model matching ``name`` (exact, case-insensitive, or basename).

        Raises:
            RuntimeError: If the upstream is unreachable, or nothing matches (the message
                lists what the upstream actually serves).
        """
        rows = self.models()
        want = name.strip().lower()
        want_base = want.rsplit("/", 1)[-1]
        match = next(
            (r for r in rows if r.name.lower() == want or r.name.lower().rsplit("/", 1)[-1] == want_base),
            None,
        )
        if match is None:
            served = ", ".join(r.name for r in rows) or "(nothing)"
            raise RuntimeError(f"{name!r} is not served by {self._upstream} — available: {served}")
        with self._lock:
            self._selected = match
            self._last_error = None

    def select_loaded(self) -> None:
        """Best-effort: select the model the upstream already has loaded (startup default).

        A router hot-swaps on request, so defaulting to anything else would evict what the
        user has running the moment the first message goes out. No loaded model (or an
        unreachable upstream) just leaves nothing selected — the UI then offers the picker.
        """
        for attempt in range(3):
            try:
                rows = self.models()
            except RuntimeError as exc:
                with self._lock:
                    self._last_error = str(exc)
                if attempt < 2:
                    time.sleep(1.0)  # a slow upstream at startup is common; give it a moment
                    self._models_at = 0.0  # don't serve the (empty) cache on the retry
                    continue
                return
            match = next((r for r in rows if r.loaded), None)
            with self._lock:
                self._last_error = None
                if match is not None:
                    self._selected = match
            return

    def touch(self) -> None:
        """Mark the upstream as just-seen-alive (e.g. a generation is actively streaming)."""
        self._ready_at = time.monotonic()

    async def ready(self) -> bool:
        """Whether the upstream answers its ``/v1/models`` (paced; see the class pacing note)."""
        now = time.monotonic()
        if now - self._ready_at < self._READY_TTL:
            return True
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=4.0)
        try:
            resp = await self._http.get(f"{self._upstream}/v1/models")
            ok = resp.status_code < 500
        except httpx.HTTPError:
            ok = False
        if ok:
            self._ready_at = now
            return True
        # One slow/failed probe shortly after a good one is jitter, not an outage.
        return now - self._ready_at < self._READY_GRACE

    async def state(self) -> ServerState:
        """Coarse lifecycle state for the UI (an unreachable upstream reads as stopped)."""
        if not await self.ready():
            self._last_error = f"upstream {self._upstream} unreachable"
            return ServerState.stopped
        return ServerState.ready if self._selected is not None else ServerState.stopped

    def stop(self) -> None:
        """Clear the selection. The remote keeps running — nothing is unloaded there."""
        with self._lock:
            self._selected = None
