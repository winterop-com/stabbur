"""A named seam in front of the model backend the serving routes talk to.

Today a stabbur server has exactly one backend: a :class:`~stabbur.server.ServerManager`
(local runtime processes) **or** an :class:`~stabbur.server.UpstreamManager` (a remote
OpenAI ``/v1``). The routes hold whichever one ``create_app`` picked and duck-type across
them. :class:`Backends` wraps that single backend and re-exposes the same surface, so the
place that has to learn about *several* backends later is this class rather than
``routers/serving/{proxy,chat,core,_base}.py``.

Step 1 of the ROADMAP's "Multiple backends at once" is deliberately a no-op: one backend
in, the same behaviour out. What step 2 has to settle first (see ROADMAP.md), because each
answer changes this class's shape rather than the routes':

- **Where backends are declared** — a repeatable ``--upstream`` carries no name, so
  ``[[backends]]`` in ``stabbur.toml`` / the machine config is likely, with the local
  library an implicit backend. That decides what the constructor takes.
- **Whether "loaded" stays singular** — one active backend at a time keeps ``current``,
  ``base_url`` and the ``/v1`` proxy pointing at exactly one place (so the members below
  stay scalar); several resident at once forces every one of them to grow a backend
  argument, and ``/api/status`` to report a set.
- **What a down backend does to the picker** — :meth:`models` must degrade to a row
  rather than raise, which means per-backend timeouts and concurrent probing here.

Identity (a qualified ``backend:model`` name) is the other blocker, and it is *not*
this class's job: it belongs wherever a model is named — ``/api/load/{name:path}``, the
OpenAI ``model`` field, the SPA picker. This seam only has to survive that change.
"""

from stabbur.library import LibraryModel
from stabbur.server import ServerManager, ServerState, UpstreamManager, UpstreamModel

# The two backend types the facade can wrap. Not a base class: ``UpstreamManager`` was
# written as a duck-typed peer of ``ServerManager`` precisely because the two share a
# read surface and almost nothing else (no process, no library model, no context window),
# and inventing a Protocol now would have to lie about the members they do not share.
Backend = ServerManager | UpstreamManager


def build(upstream: str | None, runtime_port: int | None = None) -> "Backends":
    """The backend a given configuration asks for.

    The ONE place that turns configuration into a backend. It existed in two places before —
    the app factory and `stabbur serve`'s locked-model pre-flight, which builds a throwaway
    manager before the app exists — and step 2 adds a third caller (a `[[backends]]` list), so
    a second copy of this choice is a second thing to keep in step.

    ``upstream`` set selects the remote and never spawns a local runtime; otherwise a local
    runtime manager, whose port the process-global CLI override still wins.
    """
    from stabbur import config  # noqa: PLC0415 - lazy: config imports settings, which imports this

    if upstream:
        return Backends(UpstreamManager(upstream))
    return Backends(ServerManager(port=config.runtime_port_override() or runtime_port))


class Backends:
    """Delegate the serving routes' backend surface to exactly one backend.

    Every member is spelled out and typed rather than forwarded through
    ``__getattr__``: a member the routes rely on and this class forgets must be a type
    error at the seam, which is the only reason the seam is worth having.
    """

    def __init__(self, backend: Backend) -> None:
        """Wrap a single backend.

        Args:
            backend: The local runtime manager or remote upstream manager to delegate to.
        """
        self._backend = backend

    @property
    def backend(self) -> Backend:
        """The wrapped backend.

        The escape hatch for the handful of route checks that still branch on the
        backend *type* (``isinstance(manager, UpstreamManager)`` in the ``/v1`` proxy,
        ``/api/status``, ``/api/library`` and ``/api/load``). Those branches are what
        step 3 replaces with qualified model ids; until then they need the real object.
        """
        return self._backend

    @property
    def is_upstream(self) -> bool:
        """Whether the wrapped backend fronts a remote ``/v1`` rather than local runtimes."""
        return isinstance(self._backend, UpstreamManager)

    # --- shared surface: both backends implement these identically-shaped members ---

    @property
    def base_url(self) -> str:
        """Base URL the ``/v1`` proxy forwards to (no trailing ``/v1``)."""
        return self._backend.base_url

    @property
    def current(self) -> LibraryModel | UpstreamModel | None:
        """The loaded/selected model, or ``None``.

        The two backends return different row types — a library model has a path, size and
        format; an upstream model has only what the remote's listing reported. They agree on
        ``name``, which is all the routes read off it.
        """
        return self._backend.current

    @property
    def n_ctx(self) -> int | None:
        """Context window of the loaded model (always ``None`` upstream — the remote decides)."""
        return self._backend.n_ctx

    @property
    def last_error(self) -> str | None:
        """Why the backend last looked unhealthy, else ``None``."""
        return self._backend.last_error

    async def state(self) -> ServerState:
        """Coarse lifecycle state for the UI."""
        return await self._backend.state()

    def stop(self) -> None:
        """Stop the local runtime, or clear the upstream selection.

        Shared in name only, and intentionally left that way: locally this kills a child
        process, upstream it drops a selection and the remote keeps running. Both mean
        "nothing is loaded here any more", which is what the caller (``/api/unload``,
        lifespan teardown) is asking for.
        """
        self._backend.stop()

    # --- divergent surface ---
    #
    # Below here the two backends genuinely differ, and the difference is reported as an
    # AttributeError naming the backend — exactly what calling the missing member on the
    # bare manager raises today, so the routes' existing isinstance guards keep working
    # unchanged. Papering over it would mean inventing semantics that do not exist: there
    # is no local equivalent of "list what the remote serves" (the library scan is a
    # different module, not a backend call) and no remote equivalent of "spawn a runtime
    # for this library model with this context". Unifying them is step 3's job, once a
    # qualified model id makes ``load(name)`` mean the same thing on both.

    def _local(self, member: str) -> ServerManager:
        """The wrapped backend as a local runtime manager.

        Takes the member name for the same reason ``_remote`` does: with it hardcoded, the
        message names ``load()`` whatever was actually called, and would start lying the day a
        second local-only member lands.

        Raises:
            AttributeError: If the wrapped backend is a remote upstream.
        """
        if not isinstance(self._backend, ServerManager):
            raise AttributeError(f"{member} is local-only; an upstream backend selects an id with load_by_name()")
        return self._backend

    def _remote(self, member: str) -> UpstreamManager:
        """The wrapped backend as a remote upstream manager.

        Args:
            member: Name of the member being called, for the error message.

        Raises:
            AttributeError: If the wrapped backend is a local runtime manager.
        """
        if not isinstance(self._backend, UpstreamManager):
            raise AttributeError(f"{member}() is upstream-only; the local backend has no remote to ask")
        return self._backend

    def load(self, model: LibraryModel, n_ctx: int | None = None) -> None:
        """Start (or swap to) the local runtime for ``model``. Local backends only.

        Args:
            model: The library model to run.
            n_ctx: Context window to load it with; ``None`` uses the runtime's default.

        Raises:
            AttributeError: If the wrapped backend is a remote upstream.
            RuntimeError: If the runtime binary is not installed.
        """
        self._local("load()").load(model, n_ctx)

    def load_by_name(self, name: str, *, warmup: bool = True) -> None:
        """Select the remote model matching ``name``. Upstream backends only.

        Args:
            name: Matched against the remote's ids exactly, case-insensitively, or by basename.
            warmup: Make the remote actually load it before recording the selection.

        Raises:
            AttributeError: If the wrapped backend is a local runtime manager.
            RuntimeError: If the upstream is unreachable, serves no such name, or failed to load it.
        """
        self._remote("load_by_name").load_by_name(name, warmup=warmup)

    def models(self) -> list[UpstreamModel]:
        """The remote's ``GET /v1/models`` rows. Upstream backends only.

        Raises:
            AttributeError: If the wrapped backend is a local runtime manager.
            RuntimeError: If the upstream is unreachable or answers garbage.
        """
        return self._remote("models").models()

    def select_loaded(self) -> None:
        """Best-effort startup default: select whatever the remote already has resident.

        Upstream backends only.

        Raises:
            AttributeError: If the wrapped backend is a local runtime manager.
        """
        self._remote("select_loaded").select_loaded()

    def touch(self) -> None:
        """Mark the upstream as just-seen-alive. Upstream backends only.

        Raises:
            AttributeError: If the wrapped backend is a local runtime manager.
        """
        self._remote("touch").touch()
