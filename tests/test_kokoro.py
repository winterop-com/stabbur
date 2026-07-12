"""Tests for the Kokoro TTS engine helpers (pure metadata; no model download)."""

from heim.voice import kokoro


def test_voices_are_the_full_v1_set() -> None:
    vs = kokoro.voices()
    assert len(vs) == 54
    ids = [v.id for v in vs]
    assert len(set(ids)) == 54  # all unique
    assert "af_heart" in ids and "zm_yunyang" in ids


def test_voice_metadata_derived_from_prefix() -> None:
    by_id = {v.id: v for v in kokoro.voices()}
    heart = by_id["af_heart"]
    assert (heart.name, heart.language, heart.gender) == ("Heart", "American English", "female")
    george = by_id["bm_george"]
    assert (george.name, george.language, george.gender) == ("George", "British English", "male")
    xiao = by_id["zf_xiaoxiao"]
    assert (xiao.language, xiao.gender) == ("Mandarin Chinese", "female")


def test_lang_code_maps_prefix_to_espeak() -> None:
    assert kokoro.lang_code("af_heart") == "en-us"
    assert kokoro.lang_code("bm_george") == "en-gb"
    assert kokoro.lang_code("zf_xiaoxiao") == "cmn"
    assert kokoro.lang_code("jf_alpha") == "ja"
    assert kokoro.lang_code("pf_dora") == "pt-br"


def test_available_is_bool() -> None:
    assert isinstance(kokoro.available(), bool)


def test_synthesize_rejects_unknown_voice() -> None:
    # Fails fast on a bad voice id without touching the model (when the extra is
    # installed; when it isn't, it fails earlier on availability — both are fine).
    import pytest

    with pytest.raises(RuntimeError):
        kokoro.synthesize("hello", "not_a_voice")
