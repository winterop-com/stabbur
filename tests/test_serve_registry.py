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


def test_pid_reuse_record_is_swept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A live pid isn't enough: a SIGKILLed serve leaves its record and the pid can be recycled
    # onto an unrelated process. When the live cmdline no longer matches the recorded one, the
    # record is stale and must be swept (not returned as a live endpoint).
    directory = tmp_path / "serves"
    directory.mkdir()
    (directory / "4242.json").write_text(
        ServeRecord(base_url="http://x", model="pub/X", pid=4242, cmdline="kodo serve --model pub/X").model_dump_json()
    )
    monkeypatch.setattr(serve_registry, "_registry_dir", lambda: directory)
    monkeypatch.setattr(serve_registry, "_pid_alive", lambda _pid: True)  # pid is alive…
    monkeypatch.setattr(serve_registry, "_process_command", lambda _pid: "python something-else")  # …but reused
    assert serve_registry.discover("pub/X") is None
    assert not (directory / "4242.json").exists()  # the mismatched record is cleaned up


def test_legacy_record_without_cmdline_is_swept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A record written by an older kodo has no cmdline field; with no identity to verify it's
    # treated as stale and swept rather than trusted.
    directory = tmp_path / "serves"
    directory.mkdir()
    (directory / "4243.json").write_text('{"base_url": "http://x", "model": "pub/X", "pid": 4243}')
    monkeypatch.setattr(serve_registry, "_registry_dir", lambda: directory)
    monkeypatch.setattr(serve_registry, "_pid_alive", lambda _pid: True)
    assert serve_registry.discover("pub/X") is None
    assert not (directory / "4243.json").exists()
