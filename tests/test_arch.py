"""Tests for generative (chat) vs encoder/embedding classification."""

import json
from pathlib import Path

from stabbur import arch
from stabbur.models import ModelFormat


def test_architectures_are_generative() -> None:
    assert arch.architectures_are_generative(["LlamaForCausalLM"]) is True
    assert arch.architectures_are_generative(["Qwen2ForCausalLM"]) is True
    assert arch.architectures_are_generative(["T5ForConditionalGeneration"]) is True
    assert arch.architectures_are_generative(["BertModel"]) is False
    assert arch.architectures_are_generative(["Dinov2Model", "CLIPModel"]) is False
    assert arch.architectures_are_generative([]) is False


def test_config_is_generative(tmp_path: Path) -> None:
    gen = tmp_path / "gen.json"
    gen.write_text(json.dumps({"architectures": ["GemmaForCausalLM"]}))
    emb = tmp_path / "emb.json"
    emb.write_text(json.dumps({"architectures": ["BertModel"]}))

    assert arch.config_is_generative(gen) is True
    assert arch.config_is_generative(emb) is False
    assert arch.config_is_generative(tmp_path / "missing.json") is None


def test_is_generative_by_format_and_config(tmp_path: Path) -> None:
    # GGUF is always treated as generative, no config needed.
    assert arch.is_generative(ModelFormat.gguf, tmp_path) is True
    # unknown (no weights) is never generative.
    assert arch.is_generative(ModelFormat.unknown, tmp_path) is False

    # safetensors/MLX are classified from config.json.
    (tmp_path / "config.json").write_text(json.dumps({"architectures": ["BertModel"]}))
    assert arch.is_generative(ModelFormat.safetensors, tmp_path) is False
    (tmp_path / "config.json").write_text(json.dumps({"architectures": ["LlamaForCausalLM"]}))
    assert arch.is_generative(ModelFormat.mlx, tmp_path) is True
