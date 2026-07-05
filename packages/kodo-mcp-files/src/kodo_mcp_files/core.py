"""File access under one configured root — no MCP, no kodo imports.

Everything is contained to ``root`` by :func:`safe_join` (absolute paths and ``..`` escapes are
rejected), so an assistant can browse/read/search a project without reaching the rest of the disk.
Writes are off unless ``KODO_FILES_WRITABLE`` is set. Config via ``KODO_FILES_*`` env.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict

# Noise dirs skipped when listing/searching (vendored code, caches, build output, VCS).
_SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", "dist", "build", "site"}
)


class FilesSettings(BaseSettings):
    """Config via ``KODO_FILES_*`` env: which root to expose, whether writes are allowed, caps."""

    model_config = SettingsConfigDict(env_prefix="KODO_FILES_", extra="ignore")

    root: Path = Path(".")  # KODO_FILES_ROOT — the workspace the server exposes
    writable: bool = False  # KODO_FILES_WRITABLE — opt in to write_file
    max_read_bytes: int = 200_000  # cap a single read
    max_search_matches: int = 200  # cap search results


class Entry(BaseModel):
    """One directory entry."""

    model_config = ConfigDict(frozen=True)

    path: str  # relative to the root
    type: str  # "file" or "dir"
    size: int = 0  # bytes (0 for dirs)


class Match(BaseModel):
    """One search hit: a line containing the query."""

    model_config = ConfigDict(frozen=True)

    path: str
    line: int
    text: str


def safe_join(root: Path, rel: str) -> Path:
    """Join ``rel`` under ``root``, rejecting absolute paths / ``..`` escapes. ``rel=""`` is the root."""
    if Path(rel).is_absolute():
        raise ValueError(f"invalid path {rel!r}: must be relative to the root")
    resolved = (root / rel).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
        raise ValueError(f"invalid path {rel!r}: escapes the root")
    return resolved


def _rel(root: Path, path: Path) -> str:
    """The entry's path by location under root (never resolves the target — a symlink out of root can't raise)."""
    try:
        return str(path.relative_to(root)) or "."
    except ValueError:
        return path.name


def _within_root(root: Path, path: Path) -> bool:
    """Whether ``path``'s real target stays under ``root`` (a symlink escaping the root is excluded)."""
    try:
        resolved, root_resolved = path.resolve(), root.resolve()
    except OSError:
        return False
    return resolved == root_resolved or resolved.is_relative_to(root_resolved)


def list_dir(settings: FilesSettings, subdir: str = "") -> list[Entry]:
    """List the entries directly under ``subdir`` (files and dirs), sorted, skipping noise dirs."""
    target = safe_join(settings.root, subdir)
    if not target.is_dir():
        raise NotADirectoryError(f"{subdir!r} is not a directory")
    entries: list[Entry] = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name)):
        if child.is_dir() and child.name in _SKIP_DIRS:
            continue
        if child.is_symlink() and not _within_root(settings.root, child):
            continue  # a symlink pointing outside the root — don't surface it
        entries.append(
            Entry(
                path=_rel(settings.root, child),
                type="dir" if child.is_dir() else "file",
                size=child.stat().st_size if child.is_file() else 0,
            )
        )
    return entries


def read_text(settings: FilesSettings, path: str) -> str:
    """Read a text file's content (capped at ``max_read_bytes``; rejects binary/oversize files)."""
    target = safe_join(settings.root, path)
    if not target.is_file():
        raise FileNotFoundError(f"{path!r} is not a file")
    size = target.stat().st_size
    if size > settings.max_read_bytes:
        raise ValueError(f"{path!r} is {size} bytes; over the {settings.max_read_bytes}-byte read cap")
    data = target.read_bytes()
    if b"\x00" in data:
        raise ValueError(f"{path!r} looks binary")
    return data.decode("utf-8", errors="replace")


def _is_probably_text(path: Path) -> bool:
    try:
        return b"\x00" not in path.read_bytes()[:4096]
    except OSError:
        return False


def search(settings: FilesSettings, query: str, subdir: str = "") -> list[Match]:
    """Find lines containing ``query`` (case-insensitive) in text files under ``subdir``."""
    base = safe_join(settings.root, subdir)
    needle = query.lower()
    matches: list[Match] = []
    for path in sorted(base.rglob("*")):
        if len(matches) >= settings.max_search_matches:
            break
        if any(part in _SKIP_DIRS for part in path.parts) or not path.is_file():
            continue
        if not _within_root(settings.root, path):
            continue  # reached an out-of-root file via a symlink — skip before reading its bytes
        if not _is_probably_text(path):
            continue
        try:
            for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if needle in line.lower():
                    matches.append(Match(path=_rel(settings.root, path), line=i, text=line.strip()[:300]))
                    if len(matches) >= settings.max_search_matches:
                        break
        except OSError:
            continue
    return matches


def write_text(settings: FilesSettings, path: str, content: str) -> int:
    """Write ``content`` to ``path`` (creating parent dirs). Requires ``KODO_FILES_WRITABLE``."""
    if not settings.writable:
        raise PermissionError("writes are disabled — set KODO_FILES_WRITABLE=1 to allow them")
    target = safe_join(settings.root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.write_text(content, encoding="utf-8")
