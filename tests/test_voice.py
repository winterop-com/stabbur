"""Tests for the voice registry and discovery (no models loaded, no runtime)."""

from pathlib import Path

from heim import voice
from heim.voice import Backend, VoiceKind, VoiceMode


def test_registry_is_well_formed() -> None:
    ids = [m.id for m in voice.BUILTIN]
    assert len(ids) == len(set(ids))  # ids unique
    assert {"kokoro", "chatterbox", "whisper"} <= set(ids)
    # Every TTS model declares a voice_mode; STT uses none.
    for m in voice.BUILTIN:
        if m.kind is VoiceKind.tts:
            assert m.voice_mode is not VoiceMode.none
        else:
            assert m.voice_mode is VoiceMode.none


def test_csm_is_a_cloneable_model() -> None:
    csm = voice.get("csm")
    assert csm is not None
    assert csm.voice_mode is VoiceMode.clone  # the voice comes from a reference clip
    assert csm.cloneable


def test_kokoro_is_the_lightweight_chat_voice() -> None:
    chat = voice.chat_voice()
    assert chat.id == "kokoro"
    assert chat.chat_default and chat.voice_mode is VoiceMode.preset
    assert chat.backend is Backend.kokoro_onnx


def test_lookup_helpers() -> None:
    assert voice.by_repo("mlx-community/chatterbox-fp16") is voice.get("chatterbox")
    assert voice.get("nope") is None


def test_discover_reports_presence(tmp_path: Path, monkeypatch: object) -> None:
    # Point the HF cache at an empty temp dir so discovery is deterministic (no real cache).
    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    # A library with one voice model present, as a directory under voice/<repo>.
    lib = tmp_path / "lib"
    cb_dir = voice.voice_dir(lib) / "mlx-community/chatterbox-fp16"
    cb_dir.mkdir(parents=True)
    (cb_dir / "model.safetensors").write_bytes(b"x" * 2048)

    found = {p.spec.id: p for p in voice.discover(lib)}
    assert found["chatterbox"].in_library and found["chatterbox"].library_bytes == 2048
    assert found["chatterbox"].location == "library"
    assert not found["kokoro"].available  # nothing downloaded for it here
    assert found["kokoro"].location == "not downloaded"


def test_import_copies_from_cache_and_prunes(tmp_path: Path, monkeypatch: object) -> None:
    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    # Mimic the HF cache layout: snapshots/<commit>/ holds the files, refs/main pins the commit.
    repo_cache = voice.hf_hub_cache() / "models--mlx-community--Kokoro-82M-bf16"
    (repo_cache / "snapshots" / "abc123").mkdir(parents=True)
    (repo_cache / "snapshots" / "abc123" / "model.bin").write_bytes(b"x" * 4096)
    (repo_cache / "refs").mkdir()
    (repo_cache / "refs" / "main").write_text("abc123")
    lib = tmp_path / "lib"

    from heim.voice import importer

    presence = next(p for p in voice.discover(lib) if p.spec.id == "kokoro")
    assert presence.in_cache and not presence.in_library

    result = importer.import_to_library(presence, lib, prune_cache=True)
    assert result.copied_bytes == 4096 and result.cache_pruned
    assert presence.cache_path is not None and not presence.cache_path.exists()  # cache pruned
    assert (lib / "voice" / "mlx-community/Kokoro-82M-bf16" / "model.bin").read_bytes() == b"x" * 4096
    # Re-discovering now finds it in the library.
    assert next(p for p in voice.discover(lib) if p.spec.id == "kokoro").location == "library"


def test_pull_copies_from_another_library_without_downloading(tmp_path: Path, monkeypatch: object) -> None:
    # A model already in @shared must be copied into the project-local library, not re-downloaded.
    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))  # empty HF cache -> a download would be the only fallback
    from heim.voice import importer

    shared = tmp_path / "shared"
    src = voice.voice_dir(shared) / "mlx-community/Kokoro-82M-bf16"
    src.mkdir(parents=True)
    (src / "model.bin").write_bytes(b"y" * 4096)
    target = tmp_path / "proj"  # project-local library, initially empty

    # roots() in scope: project-local target first, then the shared archive.
    monkeypatch.setattr("heim.library.roots", lambda settings=None: [target, shared])

    result = importer.pull_to_library("kokoro", target)
    assert result.copied_from == src  # copied library->library
    assert not result.downloaded and result.copied_bytes == 4096 and result.file_count == 1
    assert (target / "voice" / "mlx-community/Kokoro-82M-bf16" / "model.bin").read_bytes() == b"y" * 4096
    assert next(p for p in voice.discover(target) if p.spec.id == "kokoro").location == "library"
