"""Tests for model-recommended sampling (generation_config.json reader)."""

import json
from pathlib import Path

from kodo import sampling
from kodo.library import LibraryModel
from kodo.models import ModelFormat


def _mlx_model(model_dir: Path) -> LibraryModel:
    # MLX load_target is the directory itself.
    return LibraryModel(name="pub/M", model_format=ModelFormat.mlx, path=model_dir, load_target=model_dir)


def test_reads_generation_config(tmp_path: Path) -> None:
    (tmp_path / "generation_config.json").write_text(
        json.dumps({"temperature": 1.0, "top_p": 0.95, "top_k": 20, "repetition_penalty": 1.1})
    )
    s = sampling.recommended(_mlx_model(tmp_path))
    assert s.temperature == 1.0
    assert s.top_p == 0.95
    assert s.top_k == 20
    assert s.repeat_penalty == 1.1  # repetition_penalty mapped to repeat_penalty


def test_missing_config_is_all_none(tmp_path: Path) -> None:
    assert sampling.recommended(_mlx_model(tmp_path)) == sampling.ModelSampling()


def test_ignores_unset_sentinels(tmp_path: Path) -> None:
    # temperature 0 / top_p 1 / repeat_penalty 1 are "no-op" sentinels — don't
    # surface them (they would otherwise force greedy / disable nucleus sampling).
    (tmp_path / "generation_config.json").write_text(
        json.dumps({"temperature": 0, "top_p": 1.0, "top_k": 0, "repeat_penalty": 1.0})
    )
    assert sampling.recommended(_mlx_model(tmp_path)) == sampling.ModelSampling()


def test_gguf_reads_config_from_parent_dir(tmp_path: Path) -> None:
    # GGUF load_target is a file; the config sits next to it in the dir.
    (tmp_path / "generation_config.json").write_text(json.dumps({"temperature": 0.7}))
    model = LibraryModel(
        name="pub/G", model_format=ModelFormat.gguf, path=tmp_path, load_target=tmp_path / "model.gguf"
    )
    assert sampling.recommended(model).temperature == 0.7
