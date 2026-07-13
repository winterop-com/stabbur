"""The multi-target assistant registry + URL-based target selection.

A project can declare **more than one** assistant target (``[[assistants]]`` in ``heim.toml``) —
e.g. one DHIS2 instance per path on the same origin. :class:`AssistantRegistry` holds those targets
in declaration order (the first is the *primary*, the compat default), assigns each a stable,
collision-safe ``id``, and :func:`select` ranks the targets a browser tab URL falls under.

:func:`select` is the **Python twin of** the extension's ``tabTarget.match`` (``extension/lib/tabTarget.ts``):
same normalization (default ports dropped, trailing slashes stripped, host lower-cased, origin equality),
and a base path of ``/`` matches anything on the origin. The two implementations are pinned to parity by
**mirrored test fixtures** (``tests/test_targets.py`` here, the extension's specs there) — change one, change
both.
"""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

# Default ports a URL normalizer drops (JS ``URL.host`` omits the scheme's default port). Mirrors
# what ``tabTarget.ts`` gets for free from the browser ``URL`` so origin equality stays in parity.
_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21}


def _origin_and_path(url: str) -> tuple[str, str] | None:
    """Normalize ``url`` into ``(origin, path)`` — the Python twin of ``new URL()`` + ``normalizeOrigin``.

    Returns ``None`` when the url is unparseable (a relative url, a missing scheme/host, or a bad port) —
    the cases where ``new URL()`` throws in ``tabTarget.match`` (which then reports ``"unknown"``). The
    origin is ``scheme://host`` lower-cased with the scheme's default port dropped, exactly like the
    browser's ``URL.origin`` for http(s).
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    origin = f"{scheme}://{parts.hostname.lower()}"
    if port is not None and _DEFAULT_PORTS.get(scheme) != port:
        origin += f":{port}"
    return origin, parts.path


def _match_rank(tab_origin: str, tab_path: str, base_url: str | None) -> int | None:
    """Rank of a base url against an already-normalized tab (its base-path length), or ``None`` if no match.

    Mirrors ``tabTarget.match``: origins must be equal, then the tab path must equal the base path or sit
    under it (``base_path + "/"``); a base path of ``""`` (i.e. ``/``) matches anything on the origin. The
    returned length is the ranking key — a longer (more specific) base path wins.
    """
    if base_url is None:
        return None
    parsed = _origin_and_path(base_url)
    if parsed is None:
        return None
    base_origin, base_path_raw = parsed
    if tab_origin != base_origin:
        return None
    base_path = base_path_raw.rstrip("/")
    if base_path == "" or tab_path == base_path or tab_path.startswith(f"{base_path}/"):
        return len(base_path)
    return None


def select(url: str, registry: "AssistantRegistry") -> list[str]:
    """Target ids whose ``base_url`` the tab ``url`` falls under, ranked most-specific first.

    Python twin of the extension's ``selectTarget`` (built on ``tabTarget.match``): origin-equality plus
    longest-base-path-prefix ranking, with declaration order preserved for ties. Returns ``[]`` for an
    unparseable url or no match; a single id means an unambiguous pick, more than one means the caller must
    disambiguate (a tie the UI resolves with a picker).
    """
    parsed = _origin_and_path(url)
    if parsed is None:
        return []
    tab_origin, tab_path = parsed
    ranked: list[tuple[int, int, str]] = []
    for order, (target_id, target) in enumerate(zip(registry.ids, registry.targets, strict=True)):
        rank = _match_rank(tab_origin, tab_path, target.base_url)
        if rank is not None:
            ranked.append((rank, order, target_id))
    # Longest base path first; ties keep declaration order (ascending original index).
    ranked.sort(key=lambda r: (-r[0], r[1]))
    return [target_id for _, _, target_id in ranked]


class AssistantRegistry(BaseModel):
    """An ordered set of assistant targets; the first is the primary (the compat default).

    Holds the ``[[assistants]]`` targets in declaration order and assigns each a stable, collision-safe
    ``id`` (see :attr:`ids`). A single-``[assistant]`` project loads as a one-target registry, so every
    consumer can treat one and N targets uniformly.
    """

    model_config = ConfigDict(frozen=True)

    targets: list["AssistantInfo"] = Field(default_factory=list)

    @property
    def primary(self) -> "AssistantInfo | None":
        """The first (primary) target — the default one no-browser surfaces use; ``None`` when empty."""
        return self.targets[0] if self.targets else None

    @property
    def ids(self) -> list[str]:
        """Stable, collision-safe ids parallel to :attr:`targets`, in declaration order.

        Each id is the target's own ``id`` (slugified name, or ``target-<index>`` when unnamed); a
        duplicate is disambiguated with a ``-2`` / ``-3`` suffix so ``by_id`` is unambiguous even when two
        targets share a name.
        """
        assigned: list[str] = []
        seen: set[str] = set()
        for index, target in enumerate(self.targets):
            base = (_slugify(target.name) if target.name else "") or f"target-{index}"
            candidate, suffix = base, 1
            while candidate in seen:
                suffix += 1
                candidate = f"{base}-{suffix}"
            seen.add(candidate)
            assigned.append(candidate)
        return assigned

    def by_id(self, target_id: str) -> "AssistantInfo | None":
        """The target with this (collision-safe) id, or ``None``."""
        for assigned, target in zip(self.ids, self.targets, strict=True):
            if assigned == target_id:
                return target
        return None


# Imported at the bottom (after AssistantRegistry is defined) so the project <-> targets import cycle
# resolves under either import order: project defines AssistantInfo before importing this module, and this
# module defines AssistantRegistry before importing project back. Then resolve the forward refs.
from heim.project import AssistantInfo, _slugify  # noqa: E402

AssistantRegistry.model_rebuild()
