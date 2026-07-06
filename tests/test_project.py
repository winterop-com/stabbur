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


def test_render_manifest_round_trips(tmp_path: Path) -> None:
    # What render_manifest writes, load reads back — the writer and reader agree (A1).
    text = project.render_manifest(
        model="pub/Foo-GGUF",
        system_prompt="You are helpful.",
        mcp=[project.ProjectMcp(name="datetime", command="kodo-mcp-datetime", env={"TZ": "UTC"})],
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
    assert [m.name for m in loaded.mcp] == ["datetime"]
    assert loaded.mcp[0].env == {"TZ": "UTC"}


def test_add_mcp_appends_and_is_readable(tmp_path: Path) -> None:
    p = tmp_path / "kodo.toml"
    p.write_text(project.render_manifest(model="pub/Foo"))
    project.add_mcp(p, project.ProjectMcp(name="utils", command="kodo-mcp-utils"))
    project.add_mcp(p, project.ProjectMcp(name="files", command="kodo-mcp-files"))
    loaded = project.load(p)
    assert loaded is not None and [m.name for m in loaded.mcp] == ["utils", "files"]  # both appended, valid


def test_add_mcp_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(project.ProjectError, match="does not exist"):
        project.add_mcp(tmp_path / "nope.toml", project.ProjectMcp(name="x", command="x"))


def test_add_mcp_leaves_a_malformed_file_untouched(tmp_path: Path) -> None:
    # If appending would yield invalid TOML (here the existing file is already broken), the write
    # is refused and the file is left exactly as it was — never half-written (A1).
    p = tmp_path / "kodo.toml"
    broken = "this = = broken [[["
    p.write_text(broken)
    with pytest.raises(project.ProjectError, match="invalid TOML"):
        project.add_mcp(p, project.ProjectMcp(name="x", command="x"))
    assert p.read_text() == broken  # untouched


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
