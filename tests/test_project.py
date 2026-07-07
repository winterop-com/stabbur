"""Tests for loading the kodo.toml project manifest."""

from pathlib import Path

import pytest

from kodo import project


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert project.load(tmp_path / "kodo.toml") is None


def test_load_parses_model_and_prompt(tmp_path: Path) -> None:
    # Tools are no longer in kodo.toml (they live in .mcp.json); the manifest is model+prompt+libs.
    manifest = tmp_path / "kodo.toml"
    manifest.write_text('[project]\nmodel = "gemma-4-12B-it-QAT-GGUF"\nsystem_prompt = "Be terse."\n')
    proj = project.load(manifest)
    assert proj is not None
    assert proj.model == "gemma-4-12B-it-QAT-GGUF"
    assert proj.system_prompt == "Be terse."


def test_voice_defaults_and_toggle(tmp_path: Path) -> None:
    # chat_voice + [voice] enabled default sensibly, and parse when set.
    plain = tmp_path / "plain.toml"
    plain.write_text('[project]\nmodel = "x"\n')
    proj = project.load(plain)
    assert proj is not None and proj.chat_voice is None and proj.voice_enabled is True

    manifest = tmp_path / "kodo.toml"
    manifest.write_text('[project]\nmodel = "x"\nchat_voice = "kokoro:af_bella"\n\n[voice]\nenabled = false\n')
    proj = project.load(manifest)
    assert proj is not None
    assert proj.chat_voice == "kokoro:af_bella"
    assert proj.voice_enabled is False


def test_load_raises_projecterror_on_bad_toml(tmp_path: Path) -> None:
    p = tmp_path / "kodo.toml"
    p.write_text("this = = not toml [[[")
    with pytest.raises(project.ProjectError, match="not valid TOML"):
        project.load(p)


def test_render_manifest_round_trips(tmp_path: Path) -> None:
    # What render_manifest writes, load reads back — the writer and reader agree (A1).
    text = project.render_manifest(
        model="pub/Foo-GGUF",
        system_prompt="You are helpful.",
        local_library_dir="library",
        chat_voice="kokoro:af_heart",
    )
    p = tmp_path / "kodo.toml"
    p.write_text(text)
    loaded = project.load(p)
    assert loaded is not None
    assert loaded.model == "pub/Foo-GGUF"
    assert loaded.system_prompt == "You are helpful."
    assert loaded.chat_voice == "kokoro:af_heart"
    assert loaded.libraries == ["library", "@shared"]


def test_read_raw_is_the_single_parser(tmp_path: Path) -> None:
    # read_raw underlies both the manifest (load) and the machine settings (kodo.config), so a
    # malformed file raises one clean ProjectError rather than crashing differently in each.
    assert project.read_raw(tmp_path / "absent.toml") == {}
    p = tmp_path / "kodo.toml"
    p.write_text('library_root = "/x"\n[project]\nmodel = "m"\n')
    assert project.read_raw(p)["library_root"] == "/x"  # machine key + manifest table in one parse
    p.write_text("nope = = [[[")
    with pytest.raises(project.ProjectError, match="not valid TOML"):
        project.read_raw(p)
