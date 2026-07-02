"""Tests for the user-tag store (kodo.tags)."""

from pathlib import Path

from kodo import tags


def test_normalize_slugifies() -> None:
    assert tags.normalize("  Tested! ") == "tested"
    assert tags.normalize("Role Play") == "role-play"
    assert tags.normalize("---") == ""
    assert tags.normalize("A" * 50) == "a" * 32  # length-capped


def test_set_and_load_roundtrip(tmp_path: Path) -> None:
    saved = tags.set_tags(tmp_path, "pub/Foo-GGUF", ["Tested", "fast", "tested"])  # dedup + normalize
    assert saved == ["fast", "tested"]
    assert tags.load(tmp_path) == {"pub/Foo-GGUF": ["fast", "tested"]}
    assert tags.tags_for(tmp_path, "pub/Foo-GGUF") == ["fast", "tested"]


def test_set_empty_removes_entry(tmp_path: Path) -> None:
    tags.set_tags(tmp_path, "m", ["a"])
    tags.set_tags(tmp_path, "m", [])
    assert tags.load(tmp_path) == {}


def test_edit_adds_and_removes(tmp_path: Path) -> None:
    tags.set_tags(tmp_path, "m", ["keep", "drop"])
    result = tags.edit_tags(tmp_path, "m", add=["new"], remove=["drop"])
    assert result == ["keep", "new"]
    assert tags.tags_for(tmp_path, "m") == ["keep", "new"]


def test_load_missing_or_corrupt_is_empty(tmp_path: Path) -> None:
    assert tags.load(tmp_path) == {}  # no file
    path = tmp_path / ".kodo" / "tags.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    assert tags.load(tmp_path) == {}
