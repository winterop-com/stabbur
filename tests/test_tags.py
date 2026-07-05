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


# --- tag registry (first-class color/icon) ----------------------------------------------------


def test_valid_color() -> None:
    assert tags.valid_color("#22c55e")
    assert tags.valid_color("#fff")
    assert not tags.valid_color("22c55e")  # no hash
    assert not tags.valid_color("#22c5")  # wrong length
    assert not tags.valid_color("red")


def test_set_tag_meta_merges_only_given_fields(tmp_path: Path) -> None:
    tags.set_tag_meta(tmp_path, "Coding", color="#22c55e", icon="⌨")
    meta = tags.load_registry(tmp_path)["coding"]  # normalized key
    assert meta.color == "#22c55e"
    assert meta.icon == "⌨"
    # Updating only the description keeps the existing color/icon.
    tags.set_tag_meta(tmp_path, "coding", description="writes code")
    meta = tags.load_registry(tmp_path)["coding"]
    assert meta.color == "#22c55e" and meta.icon == "⌨" and meta.description == "writes code"


def test_registry_roundtrip_and_drops_empty(tmp_path: Path) -> None:
    tags.set_tag_meta(tmp_path, "tested", color="#3b82f6")
    tags.save_registry(tmp_path, {**tags.load_registry(tmp_path), "blank": tags.TagMeta()})  # empty entry dropped
    reg = tags.load_registry(tmp_path)
    assert set(reg) == {"tested"}
    assert reg["tested"].color == "#3b82f6"


def test_load_registry_ignores_garbage(tmp_path: Path) -> None:
    (tmp_path / ".kodo").mkdir()
    (tmp_path / ".kodo" / "tag-registry.json").write_text('{"ok": {"color": "#fff"}, "bad": 5}')
    reg = tags.load_registry(tmp_path)
    assert set(reg) == {"ok"}  # non-dict entry skipped
