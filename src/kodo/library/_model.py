"""Model identity + on-disk classification: ModelRef, LibraryModel, and format detection."""

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, computed_field

from kodo.models import ModelFormat, _human_size

if TYPE_CHECKING:
    from kodo.voice.registry import VoiceModel


# Top-level directories whose name is a layout prefix, stripped from model names.
_PREFIXES = {"gguf", "mlx", "safetensors", "huggingface", "tts", "voice", "other"}

# In a project's ``libraries`` list, this token means "the machine's default
# (shared) library" — so a committed kodo.toml stays portable (no hard-coded path).

_QUANT_PREFERENCE = ("Q4_K_M", "Q4_K_S", "Q5_K_M", "Q4_0", "Q8_0")


class ModelRef(BaseModel):
    """The identity of a library model: its ``name`` and ``model_format``.

    A model is identified by *what it is* (a name + a format), not by a bare name string. Two
    copies of the same model+format in different libraries are the *same* model (deduped to one
    on scan); a GGUF and an MLX build of the same repo are *distinct* runnable artifacts (both
    survive so ``--format`` can pick between them). Frozen + hashable so it can key a set/dict.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    model_format: ModelFormat


class LibraryModel(BaseModel):
    """A runnable model resolved from the on-drive library."""

    name: str
    model_format: ModelFormat
    generative: bool = True
    """Whether this is a generative chat LLM (vs an embedding/vision encoder)."""

    is_ollama: bool = False
    """True if this lives in the Ollama store — runnable only via Ollama, not kodo."""

    path: Path
    """Where the model lives (a directory, or the Ollama manifest)."""

    load_target: Path
    """What the runtime loads: the main GGUF file, or the MLX model directory."""

    library_root: Path = Path(".")
    """The library this model was resolved from (owns its tags/metadata)."""

    mmproj: Path | None = None
    """Multimodal projector to load alongside, if any."""

    tts: bool = False
    """True if this is a text-to-speech model (served via llama-tts, not chat)."""

    voice_kind: str = ""
    """For models in the ``voice/`` bucket: ``tts`` or ``stt`` (empty = not a voice model)."""

    vocoder: Path | None = None
    """The paired vocoder GGUF for a TTS model (e.g. WavTokenizer)."""

    languages: list[str] = []
    """Languages a TTS model supports (BCP-47-ish codes), for voice/language selection."""

    size_bytes: int = 0
    file_count: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def size_human(self) -> str:
        """Human-readable size of the model on disk."""
        return _human_size(self.size_bytes)

    @property
    def ref(self) -> ModelRef:
        """This model's identity (name + format) — the key for dedup/matching (see :class:`ModelRef`)."""
        return ModelRef(name=self.name, model_format=self.model_format)


def _weights(model_dir: Path, suffix: str) -> list[Path]:
    """Glob ``*<suffix>`` in ``model_dir``, excluding macOS ``._`` AppleDouble files."""
    return [p for p in model_dir.glob(f"*{suffix}") if not p.name.startswith("._")]


def _clean_name(rel: Path) -> str:
    """Drop a leading layout-prefix component from a relative model path."""
    parts = rel.parts
    if parts and parts[0] in _PREFIXES:
        parts = parts[1:]
    return "/".join(parts)


def _is_mlx_quantized(model_dir: Path) -> bool:
    """Whether ``config.json`` carries MLX's affine-quantization marker.

    MLX-quantized repos record ``quantization.mode == "affine"`` (with group_size/bits),
    which HF safetensors quants (gptq/awq/bnb, under ``quantization_config``) don't. This
    identifies MLX weights even outside an ``mlx/`` bucket (e.g. an ``lmstudio-community``
    MLX repo), where the path gives no hint.
    """
    try:
        quant = json.loads((model_dir / "config.json").read_text()).get("quantization")
    except (OSError, ValueError):
        return False
    return isinstance(quant, dict) and quant.get("mode") == "affine"


def _classify_dir(model_dir: Path) -> ModelFormat:
    """Classify a directory by the weight files it contains."""
    if _weights(model_dir, ".gguf"):
        return ModelFormat.gguf
    if _weights(model_dir, ".safetensors"):
        parts = model_dir.parts
        is_mlx = "mlx" in parts or "mlx-community" in parts or _is_mlx_quantized(model_dir)
        return ModelFormat.mlx if is_mlx else ModelFormat.safetensors
    return ModelFormat.unknown


# Filename hints for a vocoder GGUF (paired with a TTS model, e.g. WavTokenizer).
_VOCODER_HINTS = ("wavtokenizer", "vocoder")

# TTS models supporting more than English, by name substring → language codes.
# OuteTTS 0.1 is English-only; 0.2 adds zh/ja/ko; 0.3 adds more.
_TTS_LANGUAGES = {
    "outetts-0.3": ["en", "zh", "ja", "ko", "de", "fr", "es", "it", "nl", "pt", "pl", "ar"],
    "outetts-0.2": ["en", "zh", "ja", "ko"],
    "outetts_0.3": ["en", "zh", "ja", "ko", "de", "fr", "es", "it", "nl", "pt", "pl", "ar"],
    "outetts_0.2": ["en", "zh", "ja", "ko"],
}


def _find_vocoder(ggufs: list[Path]) -> Path | None:
    """The vocoder GGUF among ``ggufs`` (by filename hint), if any."""
    return next((g for g in ggufs if any(h in g.name.lower() for h in _VOCODER_HINTS)), None)


def _tts_languages(name: str) -> list[str]:
    """Languages a TTS model supports, inferred from its name (default English)."""
    low = name.lower()
    for key, langs in _TTS_LANGUAGES.items():
        if key in low:
            return langs
    return ["en"]


def _pick_weight(weights: list[Path]) -> Path:
    """Pick the best single weight: split-shard head, else preferred quant, else largest."""
    shard = next((g for g in weights if "00001-of-" in g.name), None)
    if shard is not None:
        return shard
    for quant in _QUANT_PREFERENCE:
        match = next((g for g in weights if quant.lower() in g.name.lower()), None)
        if match is not None:
            return match
    return max(weights, key=lambda p: p.stat().st_size)


def pick_gguf(model_dir: Path) -> tuple[Path, Path | None]:
    """Pick the main GGUF (+ optional mmproj) from a directory of ``*.gguf`` files.

    Prefers a balanced quant when several are present; falls back to the first
    shard of a split model, else the largest file. Returns ``(main, mmproj)``.
    """
    ggufs = sorted(_weights(model_dir, ".gguf"))
    mmproj = next((g for g in ggufs if g.name.lower().startswith("mmproj")), None)
    weights = [g for g in ggufs if g != mmproj]
    if not weights:
        raise FileNotFoundError(f"No .gguf weights in {model_dir}")
    return _pick_weight(weights), mmproj


def _voice_spec(name: str) -> "VoiceModel | None":
    """The registry entry for a voice model by its repo name, if known (lazy import)."""
    from kodo.voice import registry  # noqa: PLC0415 - avoid importing voice on every library import

    return registry.by_repo(name)
