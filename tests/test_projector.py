"""Tests for finding a model's multimodal projector.

The bug these cover: the projector used to be recognised only by a filename *starting* with
``mmproj``. Repos that name it otherwise (``<model>-mmproj-f16.gguf``, or nothing suggestive at
all) yielded no projector, so llama-server was started without ``--mmproj`` and the model
silently could not see or hear — the suspected cause of audio-specialist models ignoring audio.
Worse, the unrecognised projector stayed in the weight list, where it can be loaded *as* the model.
"""

import struct
from pathlib import Path

from stabbur.library._model import find_projector, pick_gguf, weight_variants
from stabbur.models import ModelFormat


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


def test_two_quants_in_one_directory_count_as_two_variants(tmp_path: Path) -> None:
    # THE BUG THIS EXISTS FOR: a repo pulled at two quants scans as ONE model whose `size_bytes`
    # is the sum of both files, so the browser advertised the pair's total for a Load that runs
    # exactly one of them. Counting the variants is what lets a card say so.
    _gguf(tmp_path / "model-Q4_K_M.gguf", "llama", size=500)
    _gguf(tmp_path / "model-Q8_0.gguf", "llama", size=900)
    _gguf(tmp_path / "mmproj-model-f16.gguf", "clip", size=50)
    variants = weight_variants(tmp_path, ModelFormat.gguf)
    assert [p.name for p in variants] == ["model-Q4_K_M.gguf", "model-Q8_0.gguf"]  # not the projector


def test_a_split_model_is_one_variant_not_three(tmp_path: Path) -> None:
    # The shards are pieces of one model, not alternatives to it: only the head stands for it.
    for i in (1, 2, 3):
        _gguf(tmp_path / f"model-0000{i}-of-00003.gguf", "llama", size=500)
    assert [p.name for p in weight_variants(tmp_path, ModelFormat.gguf)] == ["model-00001-of-00003.gguf"]


def test_a_safetensors_repo_has_no_variants_to_choose_between(tmp_path: Path) -> None:
    # An MLX/safetensors repo is one model spread over shards; the whole directory loads, so
    # there is nothing for a card to disambiguate and the count stays at one.
    (tmp_path / "model-00001-of-00002.safetensors").write_bytes(b"x" * 10)
    (tmp_path / "model-00002-of-00002.safetensors").write_bytes(b"x" * 10)
    assert weight_variants(tmp_path, ModelFormat.safetensors) == []
