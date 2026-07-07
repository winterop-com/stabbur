"""Tests for kodo serve discovery (kodo.serve_registry)."""

from pathlib import Path

import pytest

from kodo.runtime import serve_registry
from kodo.runtime.serve_registry import ServeRecord


def test_register_discover_unregister(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve_registry, "_registry_dir", lambda: tmp_path / "serves")
    serve_registry.register("http://127.0.0.1:8000", "pub/X")  # this process's pid — alive
    found = serve_registry.discover("pub/X")
    assert found is not None and found.base_url == "http://127.0.0.1:8000"
    assert serve_registry.discover("pub/Y") is None  # a different model isn't a match
    serve_registry.unregister()
    assert serve_registry.discover("pub/X") is None


def test_stale_record_is_swept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "serves"
    directory.mkdir()
    (directory / "999999.json").write_text(
        ServeRecord(base_url="http://x", model="pub/X", pid=999999).model_dump_json()
    )
    monkeypatch.setattr(serve_registry, "_registry_dir", lambda: directory)
    monkeypatch.setattr(serve_registry, "_pid_alive", lambda _pid: False)  # simulate the serve being gone
    assert serve_registry.discover("pub/X") is None
    assert not (directory / "999999.json").exists()  # the dead record is cleaned up


def test_discover_missing_dir_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve_registry, "_registry_dir", lambda: tmp_path / "nope")
    assert serve_registry.discover("pub/X") is None
