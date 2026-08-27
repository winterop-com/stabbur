"""Tests for installing canonical library models into runtimes (consumers)."""

from __future__ import annotations

from pathlib import Path

import pytest

from stabbur import consumers
from stabbur.library import LibraryModel
from stabbur.models import ModelFormat


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


# --- LM Studio consumer (zero-copy symlink) ---------------------------------------------------


def _model(tmp_path: Path, fmt: ModelFormat, name: str = "pub/Foo-GGUF") -> LibraryModel:
    libdir = tmp_path / "lib" / name
    libdir.mkdir(parents=True)
    weight = libdir / ("m.gguf" if fmt is ModelFormat.gguf else "m.safetensors")
    weight.write_bytes(b"w")
    return LibraryModel(name=name, model_format=fmt, path=libdir, load_target=weight)


def test_install_lmstudio_symlinks_the_library_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lms = tmp_path / "lmstudio"
    monkeypatch.setattr(consumers, "lmstudio_models_dir", lambda: lms)
    model = _model(tmp_path, ModelFormat.gguf, "pub/Foo-GGUF")

    result = consumers.install_lmstudio(model)
    link = lms / "pub" / "Foo-GGUF"

    assert result.runtime == "lmstudio"
    assert link.is_symlink()
    assert link.resolve() == model.path.resolve()  # points at the canonical copy (no data copied)
    assert not (link.resolve() / "m.gguf").is_symlink()  # the weight itself is the real file


def test_install_lmstudio_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consumers, "lmstudio_models_dir", lambda: tmp_path / "lmstudio")
    model = _model(tmp_path, ModelFormat.mlx, "mlx-community/Bar-4bit")  # MLX is linkable too
    consumers.install_lmstudio(model)
    again = consumers.install_lmstudio(model)
    assert "already linked" in again.detail


def test_install_lmstudio_rejects_ollama_and_safetensors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consumers, "lmstudio_models_dir", lambda: tmp_path / "lmstudio")
    ollama = LibraryModel(
        name="gemma3", model_format=ModelFormat.gguf, path=tmp_path, load_target=tmp_path, is_ollama=True
    )
    with pytest.raises(RuntimeError, match="Ollama"):
        consumers.install_lmstudio(ollama)
    st = LibraryModel(name="pub/Full", model_format=ModelFormat.safetensors, path=tmp_path, load_target=tmp_path)
    with pytest.raises(RuntimeError, match="loose GGUF/MLX"):
        consumers.install_lmstudio(st)


def test_install_lmstudio_refuses_to_clobber_a_real_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lms = tmp_path / "lmstudio"
    monkeypatch.setattr(consumers, "lmstudio_models_dir", lambda: lms)
    real = lms / "pub" / "Foo-GGUF"  # LM Studio already has its own copy here
    real.mkdir(parents=True)
    (real / "existing.gguf").write_bytes(b"x")
    with pytest.raises(RuntimeError, match="already exists"):
        consumers.install_lmstudio(_model(tmp_path, ModelFormat.gguf, "pub/Foo-GGUF"))


def test_build_modelfile_multiline_system_uses_triple_quotes(tmp_path: Path) -> None:
    # S-L5: a multi-line system prompt needs Ollama's triple-quote block, not a broken single-quote line.
    model = _gguf(tmp_path)
    mf = consumers.build_modelfile(model, system="line one\nline two")
    assert 'SYSTEM """line one\nline two"""' in mf


# --- uninstall + installed? (the consumer lifecycle: install → installed → uninstall) ---


def _lms_model(root: Path, name: str = "unsloth/Qwen3.5-4B-GGUF") -> LibraryModel:
    d = root / "gguf" / name
    d.mkdir(parents=True)
    (d / "model.gguf").write_bytes(b"gguf")
    return LibraryModel(
        name=name, model_format=ModelFormat.gguf, path=d, load_target=d / "model.gguf", library_root=root
    )


def test_lmstudio_install_then_uninstall_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lms = tmp_path / "lmstudio"
    monkeypatch.setattr(consumers, "lmstudio_models_dir", lambda: lms)
    root = tmp_path / "lib"
    model = _lms_model(root)

    consumers.install_lmstudio(model)  # symlink into LM Studio
    link = lms / model.name
    assert link.is_symlink() and link.resolve() == model.path.resolve()
    assert consumers.lmstudio_linked_names([root]) == {model.name}  # detected as stabbur-linked

    consumers.uninstall_lmstudio(model)
    assert not link.exists()  # link gone
    assert model.path.exists()  # library copy untouched
    assert consumers.lmstudio_linked_names([root]) == set()


def test_uninstall_lmstudio_refuses_a_real_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lms = tmp_path / "lmstudio"
    monkeypatch.setattr(consumers, "lmstudio_models_dir", lambda: lms)
    model = _lms_model(tmp_path / "lib")
    real = lms / model.name  # a real LM Studio download (a dir, not our symlink)
    real.mkdir(parents=True)
    (real / "model.gguf").write_bytes(b"lmstudio-own")
    with pytest.raises(RuntimeError, match="real LM Studio download"):
        consumers.uninstall_lmstudio(model)
    assert real.is_dir()  # left alone


def test_uninstall_lmstudio_errors_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consumers, "lmstudio_models_dir", lambda: tmp_path / "lmstudio")
    with pytest.raises(RuntimeError, match="isn't linked"):
        consumers.uninstall_lmstudio(_lms_model(tmp_path / "lib"))


def test_lmstudio_linked_names_ignores_real_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lms = tmp_path / "lmstudio"
    monkeypatch.setattr(consumers, "lmstudio_models_dir", lambda: lms)
    (lms / "pub" / "real-download").mkdir(parents=True)  # a real dir, not a link → not counted
    assert consumers.lmstudio_linked_names([tmp_path / "lib"]) == set()


class _FakeProc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def test_ollama_installed_names_parses_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consumers, "ollama_daemon_up", lambda: True)
    out = "NAME               ID   SIZE     MODIFIED\nqwen3.5-4b:latest  a    2.6 GB   1h\ngemma3:12b  b  8 GB  now\n"
    monkeypatch.setattr(consumers.subprocess, "run", lambda *a, **k: _FakeProc(stdout=out))
    assert consumers.ollama_installed_names() == {"qwen3.5-4b", "gemma3"}  # tags stripped, header skipped


def test_ollama_installed_names_empty_when_daemon_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consumers, "ollama_daemon_up", lambda: False)
    assert consumers.ollama_installed_names() == set()


def test_uninstall_ollama_runs_rm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consumers, "ollama_available", lambda: True)
    monkeypatch.setattr(consumers, "ollama_daemon_up", lambda: True)
    calls: list[list[str]] = []

    def _run(cmd: list[str], **kwargs: object) -> _FakeProc:
        calls.append(cmd)
        return _FakeProc()

    monkeypatch.setattr(consumers.subprocess, "run", _run)
    result = consumers.uninstall_ollama("qwen3.5-4b")
    assert calls == [["ollama", "rm", "qwen3.5-4b"]]
    assert result.runtime == "ollama" and result.name == "qwen3.5-4b"


def test_uninstall_ollama_surfaces_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consumers, "ollama_available", lambda: True)
    monkeypatch.setattr(consumers, "ollama_daemon_up", lambda: True)
    monkeypatch.setattr(consumers.subprocess, "run", lambda *a, **k: _FakeProc(returncode=1))
    with pytest.raises(RuntimeError, match="ollama rm"):
        consumers.uninstall_ollama("ghost")


# --- a --name install stays discoverable (the sidecar remembers what nothing derives) ---


def test_install_ollama_records_a_custom_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `install --to ollama --name custom` registers a name no rule derives from the model's own,
    # so without a record `library installed` shows nothing and there is no way to find what to
    # uninstall. Ollama keeps no back-reference to the library copy, so the record has to be ours.
    model = _lms_model(tmp_path / "lib")
    monkeypatch.setattr(consumers, "ollama_available", lambda: True)
    monkeypatch.setattr(consumers, "ollama_daemon_up", lambda: True)
    monkeypatch.setattr(consumers.subprocess, "run", lambda *a, **k: _FakeProc())

    consumers.install_ollama(model, name="my-assistant")
    assert consumers.recorded_install_names(model, "ollama") == ["my-assistant"]

    # A second install under another name is remembered too, and neither is duplicated.
    consumers.install_ollama(model, name="my-other")
    consumers.install_ollama(model, name="my-assistant")
    assert consumers.recorded_install_names(model, "ollama") == ["my-assistant", "my-other"]

    consumers.forget_install(model, "ollama", "my-assistant")
    assert consumers.recorded_install_names(model, "ollama") == ["my-other"]


def test_recording_an_install_keeps_the_rest_of_the_sidecar(tmp_path: Path) -> None:
    from stabbur import cards

    model = _lms_model(tmp_path / "lib")
    cards.write_metadata(cards.sidecar_dir(model.path), {"source": "huggingface", "name": model.name})
    consumers.record_install(model, "ollama", "custom")
    meta = cards.read_metadata(cards.sidecar_dir(model.path))
    assert meta["name"] == model.name  # the provenance the pull recorded survives
    assert meta["installed_as"] == {"ollama": ["custom"]}


def test_recorded_install_names_is_empty_without_a_sidecar(tmp_path: Path) -> None:
    assert consumers.recorded_install_names(_lms_model(tmp_path / "lib"), "ollama") == []
