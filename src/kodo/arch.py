"""Classify whether a model is a *generative* (chat) LLM.

kodo runs/chats/serves generative models only. Embedding and vision encoders
(e.g. ``BertModel``, ``Dinov2Model``, CLIP) are not chattable and are excluded.

Signals:
- GGUF weights → generative (the dominant case for llama.cpp chat models).
- safetensors / MLX → read ``config.json``'s ``architectures`` and check for a
  generative head (``*ForCausalLM`` / ``*ForConditionalGeneration`` / ``*LMHeadModel``).
"""

import json
from pathlib import Path

from kodo.models import ModelFormat

# Architecture name suffixes that denote a generative (text-producing) model.
_GENERATIVE_SUFFIXES = ("ForCausalLM", "ForConditionalGeneration", "LMHeadModel")


def architectures_are_generative(architectures: list[str]) -> bool:
    """True if any architecture name has a generative head."""
    return any(a.endswith(_GENERATIVE_SUFFIXES) for a in architectures)


def config_is_generative(config_path: Path) -> bool | None:
    """Classify a HF ``config.json``. Returns None if it can't be read/parsed."""
    try:
        data = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    architectures = [str(a) for a in (data.get("architectures") or [])]
    return architectures_are_generative(architectures)


def is_generative(model_format: ModelFormat, model_dir: Path) -> bool:
    """Whether a model at ``model_dir`` is a generative chat LLM.

    GGUF is treated as generative; safetensors/MLX are classified from
    ``config.json`` (a missing/unreadable config, or an encoder architecture,
    counts as non-generative).
    """
    if model_format is ModelFormat.gguf:
        return True
    if model_format is ModelFormat.unknown:
        return False
    return bool(config_is_generative(model_dir / "config.json"))
