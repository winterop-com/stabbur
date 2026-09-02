"""Tests for the voice registry and discovery (no models loaded, no runtime)."""

from pathlib import Path

from stabbur import voice
from stabbur.voice import Backend, VoiceKind, VoiceMode


def test_registry_is_well_formed() -> None:
    ids = [m.id for m in voice.BUILTIN]
    assert len(ids) == len(set(ids))  # ids unique
    assert {"kokoro", "spark", "whisper"} <= set(ids)
    # Every TTS model declares a voice_mode; STT uses none.
    for m in voice.BUILTIN:
        if m.kind is VoiceKind.tts:
            assert m.voice_mode is not VoiceMode.none
        else:
            assert m.voice_mode is VoiceMode.none


def test_spark_is_a_seeded_cloneable_model() -> None:
    spark = voice.get("spark")
    assert spark is not None
    assert spark.voice_mode is VoiceMode.seeded  # a fresh timbre per run unless a seed is pinned
    assert spark.cloneable and spark.voices == ["female", "male"]


def test_voxcpm2_is_a_designable_cloneable_model() -> None:
    vox = voice.get("voxcpm2")
    assert vox is not None
    assert vox.voice_mode is VoiceMode.design  # the voice is described in words, not picked
    assert vox.cloneable  # it also takes a reference clip
    assert vox.sample_rate == 48000  # it is the only 48 kHz model in the registry
    # A designed voice is stochastic but seedable — measured: same seed, byte-identical audio.
    assert vox.seedable and not vox.honors_speed
    assert not vox.chat_default  # 3 GB: it never displaces Kokoro as the in-chat voice


def test_only_one_model_is_the_chat_default() -> None:
    # chat_voice() returns the first match, so a second chat_default would silently win or lose.
    assert sum(1 for m in voice.BUILTIN if m.chat_default) == 1


def test_seedable_is_not_the_same_as_seeded() -> None:
    # The seed control follows `seedable`, not the voice mode: a design model samples a fresh
    # speaker per run too, and tying the control to `voice_mode is seeded` hid the seed from it.
    seedable = {m.id for m in voice.BUILTIN if m.seedable}
    assert seedable == {"spark", "voxcpm2"}
    assert voice.get("kokoro") is not None and not voice.get("kokoro").seedable  # type: ignore[union-attr]


def test_kokoro_is_the_lightweight_chat_voice() -> None:
    chat = voice.chat_voice()
    assert chat.id == "kokoro"
    assert chat.chat_default and chat.voice_mode is VoiceMode.preset
    assert chat.backend is Backend.kokoro_onnx


def test_lookup_helpers() -> None:
    assert voice.by_repo("mlx-community/Spark-TTS-0.5B-bf16") is voice.get("spark")
    assert voice.get("nope") is None


def test_discover_reports_presence(tmp_path: Path, monkeypatch: object) -> None:
    # Point the HF cache at an empty temp dir so discovery is deterministic (no real cache).
    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    # A library with one voice model present, as a directory under voice/<repo>.
    lib = tmp_path / "lib"
    cb_dir = voice.voice_dir(lib) / "mlx-community/Spark-TTS-0.5B-bf16"
    cb_dir.mkdir(parents=True)
    (cb_dir / "model.safetensors").write_bytes(b"x" * 2048)

    found = {p.spec.id: p for p in voice.discover(lib)}
    assert found["spark"].in_library and found["spark"].library_bytes == 2048
    assert found["spark"].location == "library"
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

    from stabbur.voice import importer

    presence = next(p for p in voice.discover(lib) if p.spec.id == "kokoro")
    assert presence.in_cache and not presence.in_library

    result = importer.import_to_library(presence, lib, prune_cache=True)
    assert result.copied_bytes == 4096 and result.cache_pruned
    assert presence.cache_path is not None and not presence.cache_path.exists()  # cache pruned
    assert (lib / "voice" / "mlx-community/Kokoro-82M-bf16" / "model.bin").read_bytes() == b"x" * 4096
    # Re-discovering now finds it in the library.
    assert next(p for p in voice.discover(lib) if p.spec.id == "kokoro").location == "library"


def test_import_keeps_the_cache_copy_when_the_library_copy_is_short(tmp_path: Path, monkeypatch: object) -> None:
    # The prune used to be gated on an aggregate byte total with 5% slack, which cannot tell a
    # complete copy from a short one. A copy that isn't byte-for-byte keeps the cache copy.
    import shutil

    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    repo_cache = voice.hf_hub_cache() / "models--mlx-community--Kokoro-82M-bf16"
    (repo_cache / "snapshots" / "abc123").mkdir(parents=True)
    (repo_cache / "snapshots" / "abc123" / "model.bin").write_bytes(b"x" * 4096)
    (repo_cache / "refs").mkdir()
    (repo_cache / "refs" / "main").write_text("abc123")
    lib = tmp_path / "lib"

    from stabbur.voice import importer

    real_copytree = shutil.copytree

    def short_copytree(
        src: Path | str, dst: Path | str, *, symlinks: bool = False, dirs_exist_ok: bool = False
    ) -> Path | str:
        out = real_copytree(src, dst, symlinks=symlinks, dirs_exist_ok=dirs_exist_ok)
        (Path(dst) / "model.bin").write_bytes(b"x" * 4000)  # 96 bytes short: within the old 5% slack
        return out

    monkeypatch.setattr(shutil, "copytree", short_copytree)
    presence = next(p for p in voice.discover(lib) if p.spec.id == "kokoro")
    result = importer.import_to_library(presence, lib, prune_cache=True)

    assert result.cache_pruned is False
    assert repo_cache.exists()  # the cache copy is the only complete one — keep it


def test_voice_pull_move_reports_the_cache_prune_honestly(tmp_path: Path, monkeypatch: object) -> None:
    # catalog.pull's voice branch dropped ImportResult.cache_pruned, so ``library pull voice
    # --move`` printed "local copy KEPT - copy could not be verified" for a prune that happened.
    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    repo_cache = voice.hf_hub_cache() / "models--mlx-community--Kokoro-82M-bf16"
    (repo_cache / "snapshots" / "abc123").mkdir(parents=True)
    (repo_cache / "snapshots" / "abc123" / "model.bin").write_bytes(b"x" * 4096)
    (repo_cache / "refs").mkdir()
    (repo_cache / "refs" / "main").write_text("abc123")
    lib = tmp_path / "lib"

    from stabbur import catalog
    from stabbur.models import ModelSource

    result = catalog.pull(ModelSource.voice, "kokoro", library_root=lib, move=True)

    assert result.source_removed is True  # the cache copy really was pruned
    assert not repo_cache.exists()
    assert result.file_count == 1 and result.size_bytes == 4096


def test_pull_copies_from_another_library_without_downloading(tmp_path: Path, monkeypatch: object) -> None:
    # A model already in @shared must be copied into the project-local library, not re-downloaded.
    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))  # empty HF cache -> a download would be the only fallback
    from stabbur.voice import importer

    shared = tmp_path / "shared"
    src = voice.voice_dir(shared) / "mlx-community/Kokoro-82M-bf16"
    src.mkdir(parents=True)
    (src / "model.bin").write_bytes(b"y" * 4096)
    target = tmp_path / "proj"  # project-local library, initially empty

    # roots() in scope: project-local target first, then the shared archive.
    monkeypatch.setattr("stabbur.library.roots", lambda settings=None: [target, shared])

    result = importer.pull_to_library("kokoro", target)
    assert result.copied_from == src  # copied library->library
    assert not result.downloaded and result.copied_bytes == 4096 and result.file_count == 1
    assert (target / "voice" / "mlx-community/Kokoro-82M-bf16" / "model.bin").read_bytes() == b"y" * 4096
    assert next(p for p in voice.discover(target) if p.spec.id == "kokoro").location == "library"
