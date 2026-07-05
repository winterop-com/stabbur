"""Tests for the memory store + its settings resolution."""

from pathlib import Path

from kodo_mcp_memory.core import MemorySettings, MemoryStore


def test_set_get_roundtrip(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "notes.json")
    assert store.get("user_name") is None
    store.set("user_name", "Morten", now="2026-07-05T00:00:00+00:00")
    note = store.get("user_name")
    assert note is not None
    assert note.value == "Morten"
    assert note.updated == "2026-07-05T00:00:00+00:00"


def test_overwrite_and_delete(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "notes.json")
    store.set("k", "one")
    store.set("k", "two")  # overwrite
    assert store.get("k").value == "two"  # type: ignore[union-attr]
    assert store.delete("k") is True
    assert store.get("k") is None
    assert store.delete("k") is False  # already gone


def test_list_sorted_and_search(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "notes.json")
    store.set("banana", "a yellow fruit")
    store.set("apple", "a red fruit")
    store.set("car", "a vehicle")
    assert [n.key for n in store.notes()] == ["apple", "banana", "car"]  # sorted by key
    # search matches key or value, case-insensitive
    assert {n.key for n in store.search("FRUIT")} == {"apple", "banana"}
    assert {n.key for n in store.search("car")} == {"car"}


def test_missing_file_reads_empty(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "does-not-exist.json")
    assert store.notes() == []
    assert store.get("x") is None


def test_corrupt_file_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "notes.json"
    path.write_text("{ not json")
    assert MemoryStore(path).notes() == []  # falls back to empty rather than raising


def test_settings_path_resolution(tmp_path: Path) -> None:
    # explicit KODO_MEMORY_DIR wins
    assert MemorySettings(memory_dir=tmp_path / "mem").notes_path() == tmp_path / "mem" / "notes.json"
    # else derived under the library root
    assert (
        MemorySettings(library_root=tmp_path / "lib").notes_path()
        == tmp_path / "lib" / ".kodo" / "memory" / "notes.json"
    )
    # else a project-local fallback
    assert MemorySettings().notes_path() == Path(".kodo/memory") / "notes.json"
