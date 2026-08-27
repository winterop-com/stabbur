"""Tests for finding a model's multimodal projector.

The bug these cover: the projector used to be recognised only by a filename *starting* with
``mmproj``. Repos that name it otherwise (``<model>-mmproj-f16.gguf``, or nothing suggestive at
all) yielded no projector, so llama-server was started without ``--mmproj`` and the model
silently could not see or hear — the suspected cause of audio-specialist models ignoring audio.
Worse, the unrecognised projector stayed in the weight list, where it can be loaded *as* the model.
"""

import struct
from pathlib import Path

from stabbur.library._model import find_projector, pick_gguf


def _gguf(path: Path, arch: str, *, size: int = 0) -> Path:
    """Write a minimal GGUF carrying just ``general.architecture`` (plus optional padding)."""
    key = b"general.architecture"
    val = arch.encode()
    out = bytearray(b"GGUF")
    out += struct.pack("<I", 3)  # version
    out += struct.pack("<Q", 0)  # tensor count
    out += struct.pack("<Q", 1)  # kv count
    out += struct.pack("<Q", len(key)) + key
    out += struct.pack("<I", 8)  # string
    out += struct.pack("<Q", len(val)) + val
    out += b"\0" * size  # stand in for weights, so size-based picking has something to compare
    path.write_bytes(bytes(out))
    return path


def test_finds_a_conventionally_named_projector(tmp_path: Path) -> None:
    weights = _gguf(tmp_path / "model-Q4_K_M.gguf", "llama", size=500)
    proj = _gguf(tmp_path / "mmproj-model-f16.gguf", "clip")
    assert find_projector(sorted([weights, proj])) == proj


def test_finds_a_projector_named_after_the_model(tmp_path: Path) -> None:
    # The real shape that broke it: "mmproj" is present but not at the start, so a
    # startswith() test misses it and the file is treated as weights.
    weights = _gguf(tmp_path / "ultravox-v0_5-llama-3_1-8b-Q4_K_M.gguf", "llama", size=500)
    proj = _gguf(tmp_path / "ultravox-v0_5-llama-3_1-8b-mmproj-f16.gguf", "clip")
    assert find_projector(sorted([weights, proj])) == proj


def test_finds_a_projector_whose_name_says_nothing(tmp_path: Path) -> None:
    # Nothing in the name marks it, so the only honest signal is the file's own architecture.
    weights = _gguf(tmp_path / "model-Q4_K_M.gguf", "llama", size=500)
    proj = _gguf(tmp_path / "audio-encoder-f16.gguf", "clip")
    assert find_projector(sorted([weights, proj])) == proj


def test_a_lone_gguf_is_never_the_projector(tmp_path: Path) -> None:
    # Guards the metadata probe from turning a single-file model into "all projector, no weights".
    assert find_projector([_gguf(tmp_path / "model.gguf", "llama", size=500)]) is None


def test_split_shards_yield_no_projector(tmp_path: Path) -> None:
    shards = [_gguf(tmp_path / f"model-0000{i}-of-00003.gguf", "llama", size=500) for i in (1, 2, 3)]
    assert find_projector(sorted(shards)) is None


def test_pick_gguf_keeps_an_oddly_named_projector_out_of_the_weights(tmp_path: Path) -> None:
    # The consequence that matters: without this the projector is a weight candidate, and since
    # it carries no quant marker the largest-file rule can hand the runtime the projector.
    _gguf(tmp_path / "ultravox-Q4_K_M.gguf", "llama", size=100)
    proj = _gguf(tmp_path / "ultravox-mmproj-f16.gguf", "clip", size=900)
    main, mmproj = pick_gguf(tmp_path)
    assert mmproj == proj
    assert main.name == "ultravox-Q4_K_M.gguf"
