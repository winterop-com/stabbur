"""The persistent store behind the memory MCP server — a tiny JSON key-value file.

No MCP, no stabbur imports: just where the notes live and how they're read/written. The
store is a single human-readable JSON file so it travels with whatever holds it (a
library on a drive) and can be inspected or edited by hand.

Location (config via ``STABBUR_*`` env, resolved by :class:`MemorySettings`):

* ``STABBUR_MEMORY_DIR`` — explicit directory, if set;
* else ``<STABBUR_LIBRARY_ROOT>/.stabbur/memory`` — so memory travels with the drive, next to
  the library's other metadata (``.stabbur/tags.json``), per stabbur's no-``~/.stabbur`` rule;
* else ``./.stabbur/memory`` — a project-local fallback when no library is configured.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict


class MemorySettings(BaseSettings):
    """Where the notes file lives — resolved from ``STABBUR_*`` env (or defaults)."""

    model_config = SettingsConfigDict(env_prefix="STABBUR_", extra="ignore")

    memory_dir: Path | None = None  # STABBUR_MEMORY_DIR — explicit override
    library_root: Path | None = None  # STABBUR_LIBRARY_ROOT — memory travels with the library

    def notes_path(self) -> Path:
        """The JSON file the notes are stored in (directory created on first write)."""
        base = self.memory_dir or (
            self.library_root / ".stabbur" / "memory" if self.library_root else Path(".stabbur/memory")
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
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        # Tolerate a hand-edited store: keep only well-formed ``{key: {...}}`` entries so a stray
        # ``{"k": "a string"}`` can't crash get/notes/search with an AttributeError on ``.get``.
        return {k: v for k, v in data.items() if isinstance(v, dict)}

    def _save(self, data: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic + concurrent-safe: a per-write unique temp (FastMCP runs tools on a threadpool)
        # fsync'd before the rename, so an interleaved write can't publish a truncated store.
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(self.path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

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
