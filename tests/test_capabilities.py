"""Tests for static model-capability detection (GGUF parse + dir configs)."""

import json
import struct
from pathlib import Path

from kodo import capabilities
from kodo.library import LibraryModel
from kodo.models import ModelFormat


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
        return capabilities._detect(_model(tmp_path, ModelFormat.mlx, tmp_path)).tools

    assert caps_for("you may use these tools to help the user") is False
    assert caps_for("{% if tool_call %}...{% endif %}") is True


def test_capabilities_are_cached_in_the_sidecar(tmp_path: Path) -> None:
    # First call detects + writes .kodo/capabilities.json; later calls read it (no re-detection).
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
    # An Ollama model's `path` is the manifest FILE, so a `.kodo` under it can't be created; the
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


def test_read_gguf_string_caps_a_bogus_length() -> None:
    # N-L1: a bit-flipped uint64 length must not slurp gigabytes — read is capped.
    import io
    import struct

    from kodo import capabilities

    fh = io.BytesIO(struct.pack("<Q", 10**10) + b"hello")  # claims 10 GB, has 5 bytes
    assert capabilities._read_gguf_string(fh) == "hello"
