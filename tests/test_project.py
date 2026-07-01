"""Tests for loading the kodo.toml project manifest."""

from pathlib import Path

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
