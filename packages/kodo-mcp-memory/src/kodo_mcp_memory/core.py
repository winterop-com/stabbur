"""The persistent store behind the memory MCP server — a tiny JSON key-value file.

No MCP, no kodo imports: just where the notes live and how they're read/written. The
store is a single human-readable JSON file so it travels with whatever holds it (a
library on a drive) and can be inspected or edited by hand.

Location (config via ``KODO_*`` env, resolved by :class:`MemorySettings`):

* ``KODO_MEMORY_DIR`` — explicit directory, if set;
* else ``<KODO_LIBRARY_ROOT>/.kodo/memory`` — so memory travels with the drive, next to
  the library's other metadata (``.kodo/tags.json``), per kodo's no-``~/.kodo`` rule;
* else ``./.kodo/memory`` — a project-local fallback when no library is configured.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict


class MemorySettings(BaseSettings):
    """Where the notes file lives — resolved from ``KODO_*`` env (or defaults)."""

    model_config = SettingsConfigDict(env_prefix="KODO_", extra="ignore")

    memory_dir: Path | None = None  # KODO_MEMORY_DIR — explicit override
    library_root: Path | None = None  # KODO_LIBRARY_ROOT — memory travels with the library

    def notes_path(self) -> Path:
        """The JSON file the notes are stored in (directory created on first write)."""
        base = self.memory_dir or (
            self.library_root / ".kodo" / "memory" if self.library_root else Path(".kodo/memory")
        )
        return base / "notes.json"


class Note(BaseModel):
    """One stored note: a key, its value, and when it was last written (UTC ISO 8601)."""

    model_config = ConfigDict(frozen=True)

    key: str
    value: str
    updated: str = ""


class MemoryStore:
    """A JSON-file key-value store: set / get / delete / list / search notes.

    Reads and writes the whole file each call — fine for the small, human-scale store this
    is (an assistant's scratch memory), and it keeps the file always consistent on disk.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, dict[str, str]]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    def set(self, key: str, value: str, *, now: str = "") -> None:
        """Store (or overwrite) the note under ``key``."""
        data = self._load()
        data[key] = {"value": value, "updated": now}
        self._save(data)

    def get(self, key: str) -> Note | None:
        """The note stored under ``key``, or ``None`` if there isn't one."""
        entry = self._load().get(key)
        return Note(key=key, value=entry.get("value", ""), updated=entry.get("updated", "")) if entry else None

    def delete(self, key: str) -> bool:
        """Remove the note under ``key``; return whether it existed."""
        data = self._load()
        existed = key in data
        if existed:
            del data[key]
            self._save(data)
        return existed

    def notes(self) -> list[Note]:
        """Every note, sorted by key."""
        data = self._load()
        return [Note(key=k, value=v.get("value", ""), updated=v.get("updated", "")) for k, v in sorted(data.items())]

    def search(self, query: str) -> list[Note]:
        """Notes whose key or value contains ``query`` (case-insensitive)."""
        q = query.lower()
        return [n for n in self.notes() if q in n.key.lower() or q in n.value.lower()]
