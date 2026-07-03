"""Tests for the voice registry and discovery (no models loaded, no runtime)."""

from pathlib import Path

from kodo import voice
from kodo.voice import Backend, VoiceKind, VoiceMode


def test_registry_is_well_formed() -> None:
    ids = [m.id for m in voice.BUILTIN]
    assert len(ids) == len(set(ids))  # ids unique
    assert {"kokoro", "dia", "whisper"} <= set(ids)
    # Every TTS model declares a voice_mode; STT uses none.
    for m in voice.BUILTIN:
        if m.kind is VoiceKind.tts:
            assert m.voice_mode is not VoiceMode.none
        else:
            assert m.voice_mode is VoiceMode.none


def test_dia_is_a_seeded_cloneable_dialogue_model() -> None:
    dia = voice.get("dia")
    assert dia is not None
    assert dia.voice_mode is VoiceMode.seeded  # new voice per run unless seeded/cloned
    assert dia.cloneable and dia.multi_speaker


def test_kokoro_is_the_lightweight_chat_voice() -> None:
    chat = voice.chat_voice()
    assert chat.id == "kokoro"
    assert chat.chat_default and chat.voice_mode is VoiceMode.preset
    assert chat.backend is Backend.kokoro_onnx


def test_lookup_helpers() -> None:
    assert voice.by_repo("mlx-community/Dia-1.6B") is voice.get("dia")
    assert voice.get("nope") is None


def test_discover_reports_presence(tmp_path: Path, monkeypatch: object) -> None:
    # Point the HF cache at an empty temp dir so discovery is deterministic (no real cache).
    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    # A library with one voice model present (dia), as a directory under voice/<repo>.
    lib = tmp_path / "lib"
    dia_dir = voice.voice_dir(lib) / "mlx-community/Dia-1.6B"
    dia_dir.mkdir(parents=True)
    (dia_dir / "model.safetensors").write_bytes(b"x" * 2048)

    found = {p.spec.id: p for p in voice.discover(lib)}
    assert found["dia"].in_library and found["dia"].library_bytes == 2048
    assert found["dia"].location == "library"
    assert not found["kokoro"].available  # nothing downloaded for it here
    assert found["kokoro"].location == "not downloaded"
