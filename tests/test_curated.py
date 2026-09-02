"""Tests for the curated model sets (a table — nothing here downloads anything)."""

from stabbur import curated
from stabbur.models import ModelSource
from stabbur.voice import registry as voice_registry


def test_sets_are_well_formed() -> None:
    names = [s.name for s in curated.SETS]
    assert len(names) == len(set(names))
    assert set(names) == set(curated.names())
    for s in curated.SETS:
        assert s.entries, f"{s.name} pulls nothing"
        assert s.description.endswith("."), f"{s.name} description reads as a fragment"
        for e in s.entries:
            ModelSource(e.source)  # every entry routes through a real pull path


def test_voice_entries_name_registry_ids_that_exist() -> None:
    # A set naming a voice id the registry doesn't have is a pull that fails at the last step,
    # after the chat models have already downloaded. Deriving them keeps that impossible.
    voice_ids = {m.id for m in voice_registry.BUILTIN if m.supported}
    named = {e.name for s in curated.SETS for e in s.entries if e.source == ModelSource.voice.value}
    assert named <= voice_ids


def test_the_voice_set_is_every_runnable_voice_model() -> None:
    voice_set = curated.get("voice")
    assert voice_set is not None
    assert {e.name for e in voice_set.entries} == {m.id for m in voice_registry.BUILTIN if m.supported}


def test_setup_defaults_are_small_and_do_not_duplicate_kokoro() -> None:
    # What `stabbur setup` pulls unasked, so it stays a starting set and not a catalog: one chat
    # model and transcription. Kokoro is deliberately absent — its ONNX assets come from the
    # engine, and a `voice kokoro` entry here would download a second, unused MLX copy.
    assert len(curated.SETUP_DEFAULTS) == 2
    assert "kokoro" not in {e.name for e in curated.SETUP_DEFAULTS}
    assert any(e.source == ModelSource.voice.value for e in curated.SETUP_DEFAULTS)
    assert any(e.source == ModelSource.huggingface.value for e in curated.SETUP_DEFAULTS)


def test_every_hf_entry_pins_a_quant() -> None:
    # A multi-quant GGUF repo without an include glob downloads every quant: ~20 GB fetched to
    # obtain a 2.6 GB model. Measured the hard way — the glob is required, not an optimization.
    # (That the glob matches a real file can only be checked against the Hub, not here.)
    for s in [*curated.SETS, curated.CuratedSet(name="setup", description="x.", entries=curated.SETUP_DEFAULTS)]:
        for e in s.entries:
            if e.source == ModelSource.huggingface.value:
                assert e.include, f"{s.name}/{e.name} would pull every quant in the repo"


def test_unknown_set_is_none() -> None:
    assert curated.get("not-a-set") is None
