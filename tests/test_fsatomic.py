"""Tests for the shared atomic-write helper (stabbur.fsatomic)."""

import json
from pathlib import Path

from stabbur import fsatomic


def test_write_text_roundtrip_creates_parents(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "config.toml"
    fsatomic.write_text(target, "a = 1\n")
    assert target.read_text(encoding="utf-8") == "a = 1\n"


def test_write_text_overwrites_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    fsatomic.write_text(target, "first")
    fsatomic.write_text(target, "second")
    assert target.read_text(encoding="utf-8") == "second"
    # The unique temp file is renamed into place, never left behind.
    assert list(tmp_path.iterdir()) == [target]


def test_write_json_is_sorted_with_trailing_newline(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    fsatomic.write_json(target, {"b": [1, 2], "a": "x"})
    text = target.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert list(json.loads(text).keys()) == ["a", "b"]  # keys sorted
