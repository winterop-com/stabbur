"""Curated model sets: the validated catalog as data, so a library rebuilds in one command.

The models stabbur has actually been driven with lived only in ``docs/guides/model-catalog.md``,
as a wall of ``stabbur library pull`` lines to copy-paste. That is a fine reference page and a poor
way to fill a drive: it can't be diffed against what you already have, and a typo is a 20 GB
mistake.

A curated set is just a list of :class:`stabbur.wantlist.WantModel` entries — the same shape
``stabbur library manifest`` exports — so ``stabbur library sync <set>`` reuses the whole existing
path: diff against the library, skip what's present, pull the rest, one failure not stopping the
others. Nothing here downloads anything; it is a table.

The voice sets are **derived from the voice registry** rather than restated, so a model added there
(or flagged unsupported) can't drift out of sync with what a set pulls.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from stabbur.models import ModelFormat, ModelSource
from stabbur.voice import registry as voice_registry
from stabbur.wantlist import WantModel


class CuratedSet(BaseModel):
    """A named group of models to pull together, with a one-line description of who it's for."""

    model_config = ConfigDict(frozen=True)

    name: str  # the token typed at the CLI, e.g. "starter"
    description: str
    entries: tuple[WantModel, ...]

    @property
    def size_hint(self) -> str:
        """Rough total download, summed from the per-entry notes (informational only)."""
        total = sum(_note_gb(e.note) for e in self.entries)
        return f"~{total:.0f} GB" if total else "—"


def _note_gb(note: str) -> float:
    """The leading ``N GB`` of a note, or 0.0 when it doesn't start with a size."""
    head = note.split("·", 1)[0].strip().lstrip("~")  # registry hints read "~3.2 GB"
    number, _, unit = head.partition(" ")
    try:
        value = float(number)
    except ValueError:
        return 0.0
    return value if unit.startswith("GB") else value / 1024 if unit.startswith("MB") else 0.0


def _voice(model_id: str, note: str) -> WantModel:
    """A want entry for a registry voice model (they re-pull by short id, not repo)."""
    return WantModel(source=ModelSource.voice.value, name=model_id, note=note)


# The quant a set pulls when a GGUF repo ships several. A repo like ``unsloth/Qwen3.5-4B-GGUF``
# holds every quant from IQ3 to Q8 — pulling it unfiltered fetches ~20 GB to obtain a 2.6 GB
# model, so an entry without an include glob is a bug, not a slower default.
_QUANT = "*Q4_K_M*"
_MMPROJ = "*mmproj*"  # the vision projector, needed alongside the weights by multimodal builds


def _hf(repo: str, note: str, *, include: tuple[str, ...] = (_QUANT,)) -> WantModel:
    """A want entry for one quant of a Hugging Face repo.

    ``model_format`` is part of a want's identity (:attr:`WantModel.ident`), so an entry that omits
    it matches nothing in the library and every set re-downloads models you already have.
    """
    return WantModel(
        source=ModelSource.huggingface.value,
        name=repo,
        model_format=ModelFormat.gguf.value,
        note=note,
        include=list(include),
    )


def _voice_entries(kind: voice_registry.VoiceKind | None = None) -> tuple[WantModel, ...]:
    """Every runnable registry voice model (optionally of one kind), as want entries."""
    return tuple(
        _voice(m.id, f"{m.size_hint} · {m.display_name}")
        for m in voice_registry.BUILTIN
        if m.supported and (kind is None or m.kind is kind)
    )


# The model to reach for when one has to be chosen for you — the most capable of the validated
# builds that still runs comfortably on a laptop, with tools and vision.
MAIN_MODEL = "lmstudio-community/gemma-4-12B-it-QAT-GGUF"

# The sets. Keep them few and honest: a set is a promise that these models were run through
# stabbur end to end, which is the same bar `docs/guides/model-catalog.md` documents.
SETS: tuple[CuratedSet, ...] = (
    CuratedSet(
        name="starter",
        description="A first working library: one small tool-capable chat model, the in-chat voice, and transcription.",
        entries=(
            _hf("unsloth/Qwen3.5-4B-GGUF", "2.6 GB · small, tool-capable"),
            _voice("kokoro", "0.3 GB · the in-chat voice"),
            _voice("whisper", "1.5 GB · transcription"),
        ),
    ),
    CuratedSet(
        name="voice",
        description="Every voice model stabbur can run: speaking, voice design, cloning, and transcription.",
        entries=_voice_entries(),
    ),
    CuratedSet(
        name="chat",
        description="The main model: one capable all-rounder with tools and vision, sized for a laptop.",
        entries=(
            _hf(
                MAIN_MODEL,
                "6.7 GB · tools + vision + audio",
                # A QAT build ships one quant (Q4_0), not the usual ladder — a `*Q4_K_M*` glob
                # matches only the projector there and pulls a model with no weights.
                include=("*Q4_0*", _MMPROJ),
            ),
        ),
    ),
)

# What `stabbur setup` fetches on a fresh machine unless told not to: transcription and one small
# chat model. The speaking voice is not listed here because it is not a library model — Kokoro's
# ONNX assets live at `<root>/tts/kokoro` and are fetched through the engine (see the setup step),
# so naming it here would download a second, unused copy of Kokoro as an MLX repo.
SETUP_DEFAULTS: tuple[WantModel, ...] = (
    _hf("unsloth/Qwen3.5-4B-GGUF", "2.6 GB · small, tool-capable"),
    _voice("whisper", "1.5 GB · transcription"),
)

_BY_NAME = {s.name: s for s in SETS}


def get(name: str) -> CuratedSet | None:
    """Look up a curated set by name."""
    return _BY_NAME.get(name)


def names() -> tuple[str, ...]:
    """Every curated set name, in listing order."""
    return tuple(s.name for s in SETS)
