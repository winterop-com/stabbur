"""Library location: resolving the configured library roots (with @shared) and their state."""

from pathlib import Path

from kodo import project
from kodo.config import Settings, get_settings

SHARED_TOKEN = "@shared"


class LibraryNotConfigured(RuntimeError):
    """No library location is configured — kodo can't do anything useful without one.

    The shared/default library must be set explicitly (``KODO_LIBRARY_ROOT``) rather than
    silently falling back to a CWD-relative ``./data`` (which is meaningless for a globally
    installed CLI). A project (``kodo.toml``) that lists its own ``libraries`` counts as
    configured. Carries a ready-to-print, actionable message.
    """

    HINT = (
        "No library configured. Point kodo at your model library — set KODO_LIBRARY_ROOT:\n"
        "  export KODO_LIBRARY_ROOT=/path/to/your/library   # an external drive is ideal\n"
        "or run `kodo project init` to scaffold a project with its own library."
    )

    def __init__(self, message: str = HINT) -> None:
        super().__init__(message)


# Preferred GGUF quant when a repo ships several, most-preferred first.


def roots(settings: Settings | None = None) -> list[Path]:
    """The library roots to use, in priority order (first match wins).

    A project (``kodo.toml``) may list ``libraries`` — paths relative to the project
    dir, plus the ``@shared`` token for the machine's default library — which lets a
    project keep its own models *and* use the shared archive. Outside a project (no
    ``libraries``), the single default library (``library_root``) is used. Deduped by
    resolved path, so listing ``@shared`` and the default path doesn't double-scan.
    """
    settings = settings or get_settings()
    proj = project.load()
    entries = proj.libraries if proj and proj.libraries else [SHARED_TOKEN]
    # @shared resolves to the machine default library (KODO_LIBRARY_ROOT), which must be set
    # explicitly (``library_root is None`` means unconfigured — there is no ./data fallback). If
    # a project also ships its own (project-relative) libraries, a missing @shared simply drops
    # out and the project runs from its own store (this is what makes a `--local` project
    # self-contained). @shared only hard-fails when it's the *only* source of models — i.e.
    # free-play, or a project listing nothing local.
    shared = settings.library_root
    if SHARED_TOKEN in entries and shared is None:
        local_entries = [e for e in entries if e != SHARED_TOKEN]
        if not local_entries:
            raise LibraryNotConfigured
        entries = local_entries
    out: list[Path] = []
    seen: set[Path] = set()
    for entry in entries:
        if entry == SHARED_TOKEN:
            assert shared is not None  # guaranteed: @shared only survives the guard above when set
            base = shared
        else:
            base = (Path.cwd() / entry).expanduser()
        resolved = base.resolve()
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


def default_root(settings: Settings | None = None) -> Path:
    """The machine's default library (``KODO_LIBRARY_ROOT`` / ``@shared``), or raise.

    This is the single shared store — where pulls land by default and where runtime assets
    (the Kokoro TTS model, the HF cache) live so they travel with the drive. It ignores any
    project ``libraries`` (those are :func:`roots`); it's specifically the ``@shared`` location.
    Raises :class:`LibraryNotConfigured` when unset, so no consumer silently uses ``./data``.
    """
    settings = settings or get_settings()
    if settings.library_root is None:
        raise LibraryNotConfigured
    return settings.library_root


def configured(settings: Settings | None = None) -> bool:
    """Whether a usable library location is configured (see :func:`roots`)."""
    try:
        roots(settings)
        return True
    except LibraryNotConfigured:
        return False
