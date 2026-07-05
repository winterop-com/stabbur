"""Tests for installing canonical library models into runtimes (consumers)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kodo import consumers
from kodo.library import LibraryModel
from kodo.models import ModelFormat


def _gguf(tmp_path: Path, name: str = "unsloth/Qwen3.5-4B-GGUF") -> LibraryModel:
    target = tmp_path / "model.gguf"
    target.write_bytes(b"gguf")
    return LibraryModel(name=name, model_format=ModelFormat.gguf, path=tmp_path, load_target=target)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("unsloth/Qwen3.5-4B-GGUF", "qwen3.5-4b"),
        ("TheDrummer/Rocinante-X-12B-v1-GGUF", "rocinante-x-12b-v1"),
        ("bare-name", "bare-name"),
        ("pub/Weird Name!!GGUF", "weird-name-gguf"),
    ],
)
def test_ollama_name_sanitizes(name: str, expected: str) -> None:
    assert consumers.ollama_name(name) == expected


def test_build_modelfile_points_at_canonical_gguf(tmp_path: Path) -> None:
    model = _gguf(tmp_path)
    mf = consumers.build_modelfile(model)
    assert mf.strip() == f"FROM {model.load_target}"


def test_build_modelfile_includes_mmproj_and_system(tmp_path: Path) -> None:
    model = _gguf(tmp_path)
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"proj")
    model = model.model_copy(update={"mmproj": mmproj})
    mf = consumers.build_modelfile(model, system='Be terse. Say "hi".')
    assert f"FROM {model.load_target}" in mf
    assert f"FROM {mmproj}" in mf
    assert 'SYSTEM "Be terse. Say \\"hi\\"."' in mf


def test_install_ollama_rejects_non_gguf(tmp_path: Path) -> None:
    model = LibraryModel(name="mlx-community/Foo", model_format=ModelFormat.mlx, path=tmp_path, load_target=tmp_path)
    with pytest.raises(RuntimeError, match="GGUF only"):
        consumers.install_ollama(model)


def test_install_ollama_errors_when_binary_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consumers, "ollama_available", lambda: False)
    with pytest.raises(RuntimeError, match="not installed"):
        consumers.install_ollama(_gguf(tmp_path))


def test_install_ollama_errors_when_daemon_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consumers, "ollama_available", lambda: True)
    monkeypatch.setattr(consumers, "ollama_daemon_up", lambda: False)
    with pytest.raises(RuntimeError, match="daemon is not running"):
        consumers.install_ollama(_gguf(tmp_path))


def test_install_ollama_runs_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consumers, "ollama_available", lambda: True)
    monkeypatch.setattr(consumers, "ollama_daemon_up", lambda: True)
    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stdout = "success"
        stderr = ""

    def _fake_run(cmd: list[str], **kwargs: object) -> _Proc:
        captured["cmd"] = cmd
        modelfile = Path(cmd[cmd.index("-f") + 1]).read_text()
        captured["modelfile"] = modelfile
        return _Proc()

    monkeypatch.setattr(consumers.subprocess, "run", _fake_run)
    result = consumers.install_ollama(_gguf(tmp_path), name="my-qwen")

    assert result.runtime == "ollama"
    assert result.name == "my-qwen"
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[:3] == ["ollama", "create", "my-qwen"]
    assert "FROM " in str(captured["modelfile"])


def test_install_ollama_surfaces_create_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consumers, "ollama_available", lambda: True)
    monkeypatch.setattr(consumers, "ollama_daemon_up", lambda: True)

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "boom: bad gguf"

    monkeypatch.setattr(consumers.subprocess, "run", lambda *a, **k: _Proc())
    with pytest.raises(RuntimeError, match="boom: bad gguf"):
        consumers.install_ollama(_gguf(tmp_path))
