"""A named seam in front of the model backends the serving routes talk to.

A stabbur server holds **several** declared backends — a :class:`~stabbur.server.ServerManager`
(local runtime processes) and/or any number of :class:`~stabbur.server.UpstreamManager`
(remote OpenAI ``/v1``) — but exactly **one is active** at a time. That split is the whole
shape of this class, and it comes straight from ROADMAP.md's two decided items:

- **"Loaded" stays singular.** ``current``, ``base_url``, ``n_ctx``, ``last_error``,
  ``state`` and ``stop`` describe the *active* backend and stay scalar, so ``/v1`` keeps
  forwarding byte-for-byte to one address and ``/api/status`` keeps one answer.
- **Ids are ``model@backend``** (split on the last ``@``), so every backend needs a *name*
  — which is why the plural side is keyed by :class:`BackendSpec` rather than a bare list.

The plural side has exactly one job today: :meth:`listings`, the merged picker listing. It
probes every declared backend **concurrently with a per-backend timeout** and reports a
failure as data rather than raising — the same per-item fault isolation ``library.scan()``
already gives a corrupt model, applied one level up. One unreachable host must cost the
listing a timeout and a row, never the whole response.

Still not this class's job: resolving a qualified ``model@backend`` id to a backend +
model (that belongs wherever a model is named — ``/api/load/{name:path}``, the OpenAI
``model`` field, the SPA picker). :meth:`activate` is the hook it will pull.
"""

import asyncio
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from stabbur import library as library_ops
from stabbur.library import LibraryModel
from stabbur.server import ServerManager, ServerState, UpstreamManager, UpstreamModel

# The two backend types the facade can wrap. Not a base class: ``UpstreamManager`` was
# written as a duck-typed peer of ``ServerManager`` precisely because the two share a
# read surface and almost nothing else (no process, no library model, no context window),
# and inventing a Protocol now would have to lie about the members they do not share.
Backend = ServerManager | UpstreamManager


class BackendSpec(BaseModel):
    """One declared backend: a name, and where it lives.

    The name is the qualifier in a ``model@backend`` id (ROADMAP), so it is part of a public
    contract rather than a label: it appears in the OpenAI ``model`` field, in
    ``/api/load/{name:path}``, and in the picker. Frozen because a declared backend is
    configuration, not state — what is *loaded* is tracked separately, and stays singular.

    ``url`` of ``None`` means the local library: it is a backend like any other in the
    listing, but the only one stabbur can spawn a runtime for.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    url: str | None = None


LOCAL = "local"
"""Name given to the implicit local-library backend when configuration doesn't name it."""

PROBE_TIMEOUT = 5.0
"""Seconds one backend gets to answer :meth:`Backends.listings` before it degrades to a row.

Sized for the case the timeout exists for: a host that is *gone* rather than refusing —
a powered-off box on the LAN black-holes the connection instead of sending an RST, so
nothing fails fast and only a deadline ends the wait. Five seconds is well clear of the
1-3s a busy llama-server takes to answer ``/v1/models`` mid-generation (the reason
``UpstreamManager._LISTING_TIMEOUT`` is a much roomier 15s — that budget is right for a
model *switch* and far too long for a picker refresh the user is watching).
"""


class BackendListing(BaseModel):
    """One declared backend's contribution to the merged listing — its rows, or why it has none.

    ``models`` is the same union :attr:`Backends.current` already returns, for the same reason:
    the local backend's rows are library models (path, size, format) and a remote's are ids the
    host reported, and flattening them here would mean inventing sizes and formats that don't
    exist. The caller maps each variant to its own picker row.

    ``error`` set means the backend could not be listed *at all* — down, slow, or answering
    garbage. It is never combined with rows: a partially-listed backend is not a thing either
    manager can report.
    """

    model_config = ConfigDict(frozen=True)

    backend: str
    url: str | None = None
    models: list[LibraryModel | UpstreamModel] = []
    error: str | None = None


def build(upstream: str | None, runtime_port: int | None = None) -> "Backends":
    """The backend a given configuration asks for.

    The single-backend path: what ``serve`` and ``serve --upstream`` still ask for, and what
    the app factory and `stabbur serve`'s locked-model pre-flight (a throwaway manager built
    before the app exists) both call. :func:`declare` is the plural form; both funnel into the
    same construction so a second copy of this choice can't drift.

    ``upstream`` set selects the remote and never spawns a local runtime; otherwise a local
    runtime manager, whose port the process-global CLI override still wins.
    """
    from stabbur import config  # noqa: PLC0415 - lazy: config imports this module's types

    name = config.derive_backend_name(upstream) if upstream else LOCAL
    return declare([BackendSpec(name=name, url=upstream)], runtime_port)


def declare(specs: Sequence[BackendSpec], runtime_port: int | None = None, active: str | None = None) -> "Backends":
    """Build one backend per declared spec, with one of them active.

    The ONE place that turns configuration into backends.

    ``specs`` is in LISTING order, which is deliberately not selection order: the local library
    is listed first because that is how a picker should read, and it must not follow that a
    plain ``serve --upstream gpu-box`` starts pointed at the library. Whoever knows the intent
    passes ``active``; without it the first spec wins, which is right only when the caller has
    already put the intended one there.

    Args:
        specs: The declared backends, in listing order. ``url=None`` means the local library.
        runtime_port: Port for the local runtime; the process-global CLI override still wins.
        active: Name of the backend to start pointed at. Defaults to ``specs[0]``.

    Returns:
        The facade holding all of them.

    Raises:
        ValueError: If ``specs`` is empty, names collide, more than one local backend is
            declared — two would race for the same runtime port and the same library, and
            "the local library" is one thing, not a list (a project composes several library
            roots *inside* that one backend; see ``library.roots``) — or ``active`` names a
            backend that was not declared.
    """
    from stabbur import config  # noqa: PLC0415 - lazy: config imports settings, which imports this

    if not specs:
        raise ValueError("at least one backend must be declared")
    if sum(1 for s in specs if s.url is None) > 1:
        raise ValueError("only one local backend can be declared; extra library roots go in libraries = [...]")
    port = config.runtime_port_override() or runtime_port
    built = Backends(_manager(specs[0], port), specs[0])
    for spec in specs[1:]:
        built._attach(_manager(spec, port), spec)  # noqa: SLF001 - the plural constructor, kept off the public API
    if active is not None:
        if active not in built.names:
            raise ValueError(f"active backend {active!r} was not declared; have {', '.join(built.names)}")
        built.activate(active)
    return built


def _manager(spec: BackendSpec, runtime_port: int | None) -> Backend:
    """The manager one spec asks for: a remote if it has a URL, else the local runtime manager."""
    return UpstreamManager(spec.url) if spec.url else ServerManager(port=runtime_port)


class Backends:
    """Hold every declared backend; delegate the serving routes' surface to the active one.

    Every member is spelled out and typed rather than forwarded through
    ``__getattr__``: a member the routes rely on and this class forgets must be a type
    error at the seam, which is the only reason the seam is worth having.

    Two halves, and the line between them is ROADMAP.md's "loaded stays singular":

    - **Scalar** (``backend``, ``base_url``, ``current``, ``n_ctx``, ``last_error``,
      ``state``, ``stop``, and the whole divergent surface below) — the *active* backend,
      the one thing this stabbur is pointed at. Unchanged by holding several.
    - **Plural** (``specs``, ``names``, :meth:`listings`, :meth:`activate`) — every declared
      backend. The picker reads across all of them; loading picks one.
    """

    def __init__(self, backend: Backend, spec: BackendSpec | None = None) -> None:
        """Hold a single backend, active.

        Args:
            backend: The local runtime manager or remote upstream manager to delegate to.
            spec: What configuration declared it as. Defaults to a name derived from its
                address, so the single-backend path never has to invent one at the call site.
        """
        from stabbur import config  # noqa: PLC0415 - lazy: config imports this module's types

        declared = spec or BackendSpec(
            name=(config.derive_backend_name(backend.base_url) if isinstance(backend, UpstreamManager) else LOCAL),
            url=backend.base_url if isinstance(backend, UpstreamManager) else None,
        )
        self._backends: dict[str, Backend] = {declared.name: backend}
        self._specs: dict[str, BackendSpec] = {declared.name: declared}
        self._active: str = declared.name

    def _attach(self, backend: Backend, spec: BackendSpec) -> None:
        """Hold one more declared backend, inactive. Called only by :func:`declare`.

        Private because a Backends is configuration made concrete: it is built once from a
        declaration and then only *read* (plus :meth:`activate`). A public ``add`` would make
        the set of names mutable at runtime, and names are half of a public model id.

        Raises:
            ValueError: If ``spec.name`` is already declared — a duplicate would make
                ``model@name`` ambiguous, which is exactly what the qualifier exists to prevent.
        """
        if spec.name in self._backends:
            raise ValueError(f"duplicate backend name {spec.name!r}")
        self._backends[spec.name] = backend
        self._specs[spec.name] = spec

    # --- the plural side: every declared backend ---

    @property
    def specs(self) -> tuple[BackendSpec, ...]:
        """Every declared backend, in declaration (priority) order."""
        return tuple(self._specs.values())

    @property
    def names(self) -> tuple[str, ...]:
        """Every declared backend's name — the right-hand side of a ``model@backend`` id."""
        return tuple(self._backends)

    @property
    def name(self) -> str:
        """The active backend's name."""
        return self._active

    def activate(self, name: str) -> None:
        """Point the scalar surface (and so ``/v1``) at the named backend.

        The hook qualified-id resolution pulls: ``load`` of ``gemma-4-12b@gpu-box`` activates
        ``gpu-box`` and then loads on it. Deliberately does *not* stop the outgoing backend —
        a remote keeps whatever it holds regardless, and a local runtime is stopped by the
        load that replaces it (``ServerManager.load`` calls ``stop`` itself).

        Args:
            name: A declared backend name.

        Raises:
            KeyError: If no backend is declared under ``name``.
        """
        if name not in self._backends:
            raise KeyError(f"no backend named {name!r} — declared: {', '.join(self._backends) or '(none)'}")
        self._active = name

    async def listings(self, *, timeout: float | None = None) -> list[BackendListing]:
        """Every declared backend's rows, probed concurrently, failures reported as data.

        The merged picker listing (``/api/library``). Two properties it must have, and both
        are why this is here rather than a loop at the call site:

        - **Concurrent.** Serially, one host that is merely *slow* adds its whole timeout to
          every listing; N of them make the picker unusable. Probing is I/O, so the fan-out
          costs the slowest backend, not the sum.
        - **Fault-isolated, per backend.** A down host yields a listing with ``error`` set and
          no rows — never an exception, never an empty overall result. This is the same rule
          ``library.scan()`` applies per *model*, one level up: the healthy collection is worth
          more than a clean failure.

        Both managers list synchronously (a filesystem walk; a blocking ``httpx.get``), so each
        probe goes to a worker thread. Note the thread outlives a timeout — ``wait_for`` cancels
        the *await*, not the blocking call inside it, so a hung probe keeps a pool thread until
        its own socket timeout fires (``UpstreamManager._LISTING_TIMEOUT``, 15s). That is
        bounded and invisible to the client, which is the trade: the response leaves on time.

        Args:
            timeout: Seconds each backend gets, independently. ``None`` reads
                :data:`PROBE_TIMEOUT` at call time (so it stays one adjustable number).

        Returns:
            One listing per declared backend, in declaration order.

        Raises:
            LibraryNotConfigured: If the local backend has no library root. Not a backend
                outage but a missing setup, and its message is the hint naming the variable to
                set — degrading it to a row would bury the one thing that fixes it.
        """
        budget = PROBE_TIMEOUT if timeout is None else timeout
        listings = await asyncio.gather(*(self._listing(name, budget) for name in self._backends))
        return list(listings)

    async def _listing(self, name: str, timeout: float) -> BackendListing:
        """One backend's rows, or the reason it has none. Never raises except on missing setup."""
        spec = self._specs[name]
        try:
            models = await asyncio.wait_for(asyncio.to_thread(self._rows, name), timeout)
        except TimeoutError:
            return BackendListing(backend=name, url=spec.url, error=f"did not answer within {timeout:g}s")
        except library_ops.LibraryNotConfigured:
            raise
        except Exception as exc:  # noqa: BLE001 - fault isolation: one backend must not take down the listing
            return BackendListing(backend=name, url=spec.url, error=str(exc) or type(exc).__name__)
        return BackendListing(backend=name, url=spec.url, models=models)

    def _rows(self, name: str) -> list[LibraryModel | UpstreamModel]:
        """What one backend serves, blocking: the remote's ids, or the library it can run.

        The local backend's "listing" is the library scan rather than a manager call, because
        that is what it can load — a ``ServerManager`` knows only about the model it has
        running. Which is also why the two return different row types (see
        :class:`BackendListing`).
        """
        backend = self._backends[name]
        if isinstance(backend, UpstreamManager):
            return list(backend.models())
        return list(library_ops.scan())

    # --- the scalar side: the active backend ---

    @property
    def backend(self) -> Backend:
        """The active backend.

        The escape hatch for the handful of route checks that still branch on the
        backend *type* (``isinstance(manager, UpstreamManager)`` in the ``/v1`` proxy,
        ``/api/status``, ``/api/library`` and ``/api/load``). Those branches are what
        step 3 replaces with qualified model ids; until then they need the real object.
        """
        return self._backends[self._active]

    @property
    def is_upstream(self) -> bool:
        """Whether the active backend fronts a remote ``/v1`` rather than local runtimes."""
        return isinstance(self.backend, UpstreamManager)

    # --- shared surface: both backends implement these identically-shaped members ---

    @property
    def base_url(self) -> str:
        """Base URL the ``/v1`` proxy forwards to (no trailing ``/v1``)."""
        return self.backend.base_url

    @property
    def current(self) -> LibraryModel | UpstreamModel | None:
        """The loaded/selected model, or ``None``.

        The two backends return different row types — a library model has a path, size and
        format; an upstream model has only what the remote's listing reported. They agree on
        ``name``, which is all the routes read off it.
        """
        return self.backend.current

    @property
    def n_ctx(self) -> int | None:
        """Context window of the loaded model (always ``None`` upstream — the remote decides)."""
        return self.backend.n_ctx

    @property
    def last_error(self) -> str | None:
        """Why the backend last looked unhealthy, else ``None``."""
        return self.backend.last_error

    async def state(self) -> ServerState:
        """Coarse lifecycle state for the UI."""
        return await self.backend.state()

    def stop(self) -> None:
        """Stop the local runtime, or clear the upstream selection.

        Shared in name only, and intentionally left that way: locally this kills a child
        process, upstream it drops a selection and the remote keeps running. Both mean
        "nothing is loaded here any more", which is what the caller (``/api/unload``,
        lifespan teardown) is asking for.
        """
        self.backend.stop()

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
        if not isinstance(self.backend, ServerManager):
            raise AttributeError(f"{member} is local-only; an upstream backend selects an id with load_by_name()")
        return self.backend

    def _remote(self, member: str) -> UpstreamManager:
        """The wrapped backend as a remote upstream manager.

        Args:
            member: Name of the member being called, for the error message.

        Raises:
            AttributeError: If the wrapped backend is a local runtime manager.
        """
        if not isinstance(self.backend, UpstreamManager):
            raise AttributeError(f"{member}() is upstream-only; the local backend has no remote to ask")
        return self.backend

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
