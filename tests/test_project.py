"""Tests for loading the kodo.toml project manifest."""

from pathlib import Path

import pytest

from kodo import project


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert project.load(tmp_path / "kodo.toml") is None


def test_load_parses_model_prompt_and_mcp(tmp_path: Path) -> None:
    manifest = tmp_path / "kodo.toml"
    manifest.write_text(
        "[project]\n"
        'model = "gemma-4-12B-it-QAT-GGUF"\n'
        'system_prompt = "Be terse."\n\n'
        "[[mcp]]\n"
        'name = "datetime"\n'
        'command = "kodo-mcp-datetime"\n\n'
        "[[mcp]]\n"
        'command = "dhis2w-mcp-bridge"\n'
    )
    proj = project.load(manifest)
    assert proj is not None
    assert proj.model == "gemma-4-12B-it-QAT-GGUF"
    assert proj.system_prompt == "Be terse."
    assert [m.command for m in proj.mcp] == ["kodo-mcp-datetime", "dhis2w-mcp-bridge"]
    assert proj.mcp[0].name == "datetime"


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
    p.write_text('[[mcp]]\nname = "x"\n')  # missing required 'command'
    with pytest.raises(project.ProjectError, match="command"):
        project.load(p)
