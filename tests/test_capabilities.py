"""Tests for static model-capability detection (GGUF parse + dir configs)."""

import json
import os
import struct
from pathlib import Path

import pytest

from stabbur import capabilities
from stabbur.library import LibraryModel
from stabbur.models import ModelFormat


def _gguf_string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _write_gguf(path: Path, kv: dict[str, tuple[int, object]]) -> None:
    """Write a minimal GGUF file with the given metadata KV (type code + value)."""
    out = bytearray(b"GGUF")
    out += struct.pack("<I", 3)  # version
    out += struct.pack("<Q", 0)  # tensor count
    out += struct.pack("<Q", len(kv))  # kv count
    for key, (vtype, value) in kv.items():
        out += _gguf_string(key)
        out += struct.pack("<I", vtype)
        if vtype == 8:  # string
            out += _gguf_string(str(value))
        elif vtype == 4:  # uint32
            out += struct.pack("<I", int(value))  # type: ignore[call-overload]
        elif vtype == 7:  # bool
            out += struct.pack("<?", bool(value))
        else:  # pragma: no cover - only the types above are used in tests
            raise ValueError(vtype)
    path.write_bytes(bytes(out))


def _model(path: Path, fmt: ModelFormat, load_target: Path, mmproj: Path | None = None) -> LibraryModel:
    return LibraryModel(name="test/model", model_format=fmt, path=path, load_target=load_target, mmproj=mmproj)


def test_gguf_capabilities_reads_metadata(tmp_path: Path) -> None:
    gguf = tmp_path / "model.gguf"
    _write_gguf(
        gguf,
        {
            "general.architecture": (8, "llama"),
            "llama.context_length": (4, 32768),
            "tokenizer.chat_template": (8, "{% if tools %}...tool_call...{% endif %}"),
        },
    )
    caps = capabilities.capabilities(_model(tmp_path, ModelFormat.gguf, gguf))
    assert caps.tools is True
    assert caps.context_length == 32768
    assert caps.vision is False  # no mmproj


def test_gguf_vision_from_mmproj(tmp_path: Path) -> None:
    gguf = tmp_path / "model.gguf"
    _write_gguf(gguf, {"general.architecture": (8, "llama"), "tokenizer.chat_template": (8, "plain chat")})
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"x")
    caps = capabilities.capabilities(_model(tmp_path, ModelFormat.gguf, gguf, mmproj=mmproj))
    assert caps.vision is True
    assert caps.tools is False  # template has no tool markers


def test_gguf_missing_file_is_safe(tmp_path: Path) -> None:
    caps = capabilities.capabilities(_model(tmp_path, ModelFormat.gguf, tmp_path / "nope.gguf"))
    assert caps == capabilities.ModelCapabilities()


def test_dir_capabilities_nested_text_config(tmp_path: Path) -> None:
    # Multimodal MLX config: vision_config present, context under text_config.
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "architectures": ["Qwen3ForConditionalGeneration"],
                "vision_config": {"hidden_size": 1},
                "text_config": {"max_position_embeddings": 262144},
            }
        )
    )
    (tmp_path / "tokenizer_config.json").write_text(
        json.dumps({"chat_template": "{% if tool_calls %}{{ tool_call }}{% endif %}"})
    )
    caps = capabilities.capabilities(_model(tmp_path, ModelFormat.mlx, tmp_path))
    assert caps.vision is True
    assert caps.tools is True
    assert caps.context_length == 262144


def test_dir_capabilities_text_only(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"architectures": ["LlamaForCausalLM"], "max_position_embeddings": 8192})
    )
    (tmp_path / "tokenizer_config.json").write_text(json.dumps({"chat_template": "plain, no functions"}))
    caps = capabilities.capabilities(_model(tmp_path, ModelFormat.mlx, tmp_path))
    assert caps.vision is False
    assert caps.tools is False
    assert caps.context_length == 8192


def test_tools_needs_a_calling_marker_not_bare_tools(tmp_path: Path) -> None:
    # A template that only mentions "tools" in passing (as audio specialists do) is NOT
    # tool-capable; a real tool-calling structure (tool_call) is.
    def caps_for(template: str) -> bool:
        (tmp_path / "config.json").write_text(json.dumps({"architectures": ["LlamaForCausalLM"]}))
        (tmp_path / "tokenizer_config.json").write_text(json.dumps({"chat_template": template}))
        # _detect (not the cached capabilities()) — this re-reads a mutated model at the same path.
        caps = capabilities._detect(_model(tmp_path, ModelFormat.mlx, tmp_path))
        assert caps is not None  # a readable config: detected, not failed
        return caps.tools

    assert caps_for("you may use these tools to help the user") is False
    assert caps_for("{% if tool_call %}...{% endif %}") is True


def test_capabilities_are_cached_in_the_sidecar(tmp_path: Path) -> None:
    # First call detects + writes .stabbur/capabilities.json; later calls read it (no re-detection).
    gguf = tmp_path / "m.gguf"
    _write_gguf(gguf, {"general.architecture": (8, "llama"), "llama.context_length": (4, 8192)})
    model = _model(tmp_path, ModelFormat.gguf, gguf)
    first = capabilities.capabilities(model)
    cache = capabilities._cache_path(model)
    assert cache.is_file() and first.context_length == 8192
    # Corrupt the weights: a cached read must ignore them and return the stored result.
    gguf.write_bytes(b"garbage")
    assert capabilities.capabilities(model) == first


def test_ollama_capabilities_cache_lands_in_library_sidecar(tmp_path: Path) -> None:
    # An Ollama model's `path` is the manifest FILE, so a `.stabbur` under it can't be created; the
    # cache must go to the ollama/.library/<safe_name>/ sidecar instead. First read detects + writes
    # it there; the second read hits the cache even after the weights are corrupted.
    manifest = tmp_path / "ollama" / "manifests" / "registry.ollama.ai" / "library" / "qwen" / "latest"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    blob = tmp_path / "ollama" / "blobs" / "sha256-abc"
    blob.parent.mkdir(parents=True, exist_ok=True)
    _write_gguf(blob, {"general.architecture": (8, "llama"), "llama.context_length": (4, 4096)})
    model = LibraryModel(
        name="qwen:latest",
        model_format=ModelFormat.gguf,
        is_ollama=True,
        path=manifest,
        load_target=blob,
        library_root=tmp_path,
    )

    caps = capabilities.capabilities(model)
    cache = capabilities._cache_path(model)
    assert cache == tmp_path / "ollama" / ".library" / "qwen_latest" / capabilities._CAPS_CACHE
    assert cache.is_file() and caps.context_length == 4096
    blob.write_bytes(b"garbage")  # a cached read must ignore corrupted weights
    assert capabilities.capabilities(model) == caps


def test_gguf_audio_from_mmproj_metadata(tmp_path: Path) -> None:
    # An mmproj with clip.has_audio_encoder → audio capability (vision off).
    gguf = tmp_path / "model.gguf"
    _write_gguf(gguf, {"general.architecture": (8, "llama")})
    mmproj = tmp_path / "mmproj.gguf"
    _write_gguf(mmproj, {"clip.has_audio_encoder": (7, 1), "clip.has_vision_encoder": (7, 0)})
    caps = capabilities.capabilities(_model(tmp_path, ModelFormat.gguf, gguf, mmproj=mmproj))
    assert caps.audio is True
    assert caps.vision is False


def test_gguf_bare_mmproj_defaults_to_vision(tmp_path: Path) -> None:
    # An older mmproj with no encoder flags → assume vision (the common case).
    gguf = tmp_path / "model.gguf"
    _write_gguf(gguf, {"general.architecture": (8, "llama")})
    mmproj = tmp_path / "mmproj.gguf"
    _write_gguf(mmproj, {"general.architecture": (8, "clip")})
    caps = capabilities.capabilities(_model(tmp_path, ModelFormat.gguf, gguf, mmproj=mmproj))
    assert caps.vision is True
    assert caps.audio is False


def test_read_gguf_string_rejects_a_bogus_length() -> None:
    # N-L1: a bit-flipped uint64 length must not slurp gigabytes. It is now bounded by the end of
    # the file, so it aborts the parse (read_metadata returns what it had) instead of allocating.
    import io

    from stabbur import gguf

    data = struct.pack("<Q", 10**10) + b"hello"  # claims 10 GB, has 5 bytes
    with pytest.raises(ValueError, match="past end of file"):
        gguf._read_string(io.BytesIO(data), len(data))


# --- a failed read is not an answer, so it is never cached ---------------------------------------


def test_a_failed_gguf_read_is_not_cached_and_is_retried(tmp_path: Path) -> None:
    # The poisoning case: a scan while the drive stalls (or before the file has landed) reads
    # nothing. That must not be written to the sidecar as "no vision, no tools" — the sidecar
    # travels with the library, so one bad read would follow the model to every machine.
    model = _model(tmp_path, ModelFormat.gguf, tmp_path / "m.gguf")
    assert capabilities.capabilities(model) == capabilities.ModelCapabilities()  # conservative now
    assert not capabilities._cache_path(model).exists()  # ... but nothing recorded

    # The weights arrive; the next call re-detects instead of inheriting the failure.
    _write_gguf(tmp_path / "m.gguf", {"general.architecture": (8, "llama"), "llama.context_length": (4, 4096)})
    assert capabilities.capabilities(model).context_length == 4096


def test_a_half_written_model_dir_is_not_cached_and_is_retried(tmp_path: Path) -> None:
    # A scan racing a copy: config.json exists but is truncated. Detection fails rather than
    # recording a vision checkpoint as text-only (which routes MLX to the text-only runtime).
    (tmp_path / "config.json").write_text('{"architectures": ["Qwen3ForCon')
    model = _model(tmp_path, ModelFormat.mlx, tmp_path)
    assert capabilities.capabilities(model) == capabilities.ModelCapabilities()
    assert not capabilities._cache_path(model).exists()

    (tmp_path / "config.json").write_text(json.dumps({"vision_config": {"hidden_size": 1}}))
    assert capabilities.capabilities(model).vision is True


def test_an_empty_model_dir_is_a_failed_read_not_an_empty_model(tmp_path: Path) -> None:
    # Neither config present: the directory was not readable yet, so there is no answer to cache.
    model = _model(tmp_path, ModelFormat.mlx, tmp_path)
    assert capabilities._detect(model) is None
    assert capabilities.capabilities(model) == capabilities.ModelCapabilities()
    assert not capabilities._cache_path(model).exists()


def test_a_model_that_really_declares_nothing_is_still_cached(tmp_path: Path) -> None:
    # The other side of the distinction: a GGUF that was read fine and declares no vision, no
    # tools and no context is a real result, and caching it is the whole point of the sidecar.
    gguf = tmp_path / "m.gguf"
    _write_gguf(gguf, {"general.architecture": (8, "llama")})
    model = _model(tmp_path, ModelFormat.gguf, gguf)
    assert capabilities.capabilities(model) == capabilities.ModelCapabilities()
    cache = capabilities._cache_path(model)
    assert cache.is_file()
    assert capabilities.ModelCapabilities.model_validate_json(cache.read_text()) == capabilities.ModelCapabilities()


def test_the_cache_write_is_atomic_and_a_failed_write_is_survivable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The sidecar goes through fsatomic: an unclean eject mid-write can't leave a truncated
    # capabilities.json (which would read as a permanent cache miss), and a write that fails
    # outright still returns the detected capabilities rather than raising at the caller.
    gguf = tmp_path / "m.gguf"
    _write_gguf(gguf, {"general.architecture": (8, "llama"), "llama.context_length": (4, 8192)})
    model = _model(tmp_path, ModelFormat.gguf, gguf)

    def _drive_went_away(_fd: int) -> None:
        raise OSError("drive went away mid-write")

    monkeypatch.setattr(os, "fsync", _drive_went_away)
    assert capabilities.capabilities(model).context_length == 8192
    cache = capabilities._cache_path(model)
    assert not cache.exists()  # nothing half-written landed
    assert list(cache.parent.iterdir()) == []  # and no staging temp was left behind
