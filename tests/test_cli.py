"""CLI behavior tests (Typer CliRunner) — library-centric list + chat-only guard."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from kodo import attach, cli
from kodo import catalog as catalog_ops
from kodo import library as library_ops
from kodo.library import LibraryModel
from kodo.models import Catalog, ModelEntry, ModelFormat, ModelSource, PullResult

runner = CliRunner()


def _lib_model(name: str, *, generative: bool = True, fmt: ModelFormat = ModelFormat.gguf) -> LibraryModel:
    p = Path("/tmp") / name
    return LibraryModel(
        name=name, model_format=fmt, generative=generative, path=p, load_target=p / "w.gguf", size_bytes=1000
    )


def _entry(name: str, *, generative: bool, fmt: ModelFormat) -> ModelEntry:
    return ModelEntry(
        source=ModelSource.huggingface, name=name, model_format=fmt, generative=generative, path=Path("/tmp") / name
    )


def test_library_ls_shows_the_library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library_ops, "scan", lambda: [_lib_model("pub/Chat-GGUF")])
    result = runner.invoke(cli.app, ["library", "ls"])
    assert result.exit_code == 0, result.output
    assert "pub/Chat-GGUF" in result.output
    assert "in your library" in result.output


def test_chat_refuses_non_generative_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not in the library, but present in a source cache as an embedding model.
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [])
    monkeypatch.setattr(
        catalog_ops,
        "list_models",
        lambda *a, **k: Catalog(entries=[_entry("st/all-MiniLM", generative=False, fmt=ModelFormat.safetensors)]),
    )
    result = runner.invoke(cli.app, ["chat", "all-MiniLM"])
    assert result.exit_code == 1
    assert "not a chat model" in result.output


def test_init_writes_manifest_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Model already in the library → init skips the pull.
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("unsloth/X-GGUF")])
    with runner.isolated_filesystem():
        first = runner.invoke(cli.app, ["project", "init", "--model", "unsloth/X-GGUF"])
        assert first.exit_code == 0, first.output
        manifest = Path("kodo.toml")
        assert manifest.exists()
        text = manifest.read_text()
        assert 'model = "unsloth/X-GGUF"' in text
        assert "library_root =" in text  # library config lives in kodo.toml, not .env

        again = runner.invoke(cli.app, ["project", "init", "--model", "unsloth/X-GGUF"])
        assert again.exit_code == 1  # refuses to clobber an existing project
        assert "already exists" in again.output


def test_project_show_lists_model_prompt_and_live_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    # `project show` must surface the bound model, the system prompt, and the *actual*
    # tools (from connecting to the MCP servers) — not just server names.
    from kodo import project as project_mod

    proj = project_mod.Project(
        model="unsloth/X-GGUF",
        system_prompt="Be concise.",
        mcp=[project_mod.ProjectMcp(name="datetime", command="kodo-mcp-datetime")],
    )
    monkeypatch.setattr(project_mod, "load", lambda *a, **k: proj)
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("unsloth/X-GGUF")])
    # Stub the (network/subprocess) MCP connect so the test stays hermetic.
    monkeypatch.setattr(
        cli, "_connect_project_tools", lambda mcp: ({"datetime": [("today", "Return today's date.")]}, None)
    )
    result = runner.invoke(cli.app, ["project", "show"])
    assert result.exit_code == 0, result.output
    assert "unsloth/X-GGUF" in result.output
    assert "Be concise." in result.output
    assert "today" in result.output  # the real tool name, not just "datetime"


def test_project_show_without_manifest_hints_init(monkeypatch: pytest.MonkeyPatch) -> None:
    from kodo import project as project_mod

    monkeypatch.setattr(project_mod, "load", lambda *a, **k: None)
    result = runner.invoke(cli.app, ["project", "show"])
    assert result.exit_code == 1
    assert "kodo project init" in result.output


def _pull_result(name: str) -> PullResult:
    return PullResult(
        source=ModelSource.lmstudio, name=name, destination=Path("/tmp") / name, size_bytes=10, file_count=1
    )


def test_pull_requires_name_or_all() -> None:
    # Neither name nor --all → usage error; both → usage error.
    neither = runner.invoke(cli.app, ["library", "pull", "ollama"])
    assert neither.exit_code == 2
    assert "either a model name or --all" in neither.output
    both = runner.invoke(cli.app, ["library", "pull", "ollama", "some:model", "--all"])
    assert both.exit_code == 2


def test_pull_all_imports_missing_and_skips_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [
        _entry("pub/A-GGUF", generative=True, fmt=ModelFormat.gguf),
        _entry("pub/B-GGUF", generative=True, fmt=ModelFormat.gguf),
    ]
    monkeypatch.setattr(catalog_ops, "list_models", lambda *a, **k: Catalog(entries=entries))
    monkeypatch.setattr(library_ops, "scan", lambda: [_lib_model("pub/A-GGUF")])  # A already in library
    pulled: list[str] = []

    def fake_pull(source: ModelSource, name: str, **_: object) -> PullResult:
        pulled.append(name)
        return _pull_result(name)

    monkeypatch.setattr(catalog_ops, "pull", fake_pull)
    result = runner.invoke(cli.app, ["library", "pull", "lmstudio", "--all"])
    assert result.exit_code == 0, result.output
    assert pulled == ["pub/B-GGUF"]  # only the one missing from the library
    assert "1 imported" in result.output
    assert "1 already in library" in result.output


def test_pull_all_continues_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [
        _entry("pub/A-GGUF", generative=True, fmt=ModelFormat.gguf),
        _entry("pub/B-GGUF", generative=True, fmt=ModelFormat.gguf),
    ]
    monkeypatch.setattr(catalog_ops, "list_models", lambda *a, **k: Catalog(entries=entries))
    monkeypatch.setattr(library_ops, "scan", lambda: [])

    def fake_pull(source: ModelSource, name: str, **_: object) -> PullResult:
        if name == "pub/A-GGUF":
            raise FileNotFoundError("blob missing from the store")
        return _pull_result(name)

    monkeypatch.setattr(catalog_ops, "pull", fake_pull)
    result = runner.invoke(cli.app, ["library", "pull", "lmstudio", "--all"])
    assert result.exit_code == 1  # some failed
    assert "1 imported" in result.output
    assert "1 failed" in result.output
    assert "blob missing" in result.output


def test_pull_all_uses_exact_identity_not_bare_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # Library has alice/Foo; importing bob/Foo must NOT be skipped as "already there".
    monkeypatch.setattr(
        catalog_ops,
        "list_models",
        lambda *a, **k: Catalog(entries=[_entry("bob/Foo", generative=True, fmt=ModelFormat.gguf)]),
    )
    monkeypatch.setattr(library_ops, "scan", lambda: [_lib_model("alice/Foo")])
    pulled: list[str] = []

    def fake_pull(source: ModelSource, name: str, **_: object) -> PullResult:
        pulled.append(name)
        return _pull_result(name)

    monkeypatch.setattr(catalog_ops, "pull", fake_pull)
    result = runner.invoke(cli.app, ["library", "pull", "lmstudio", "--all"])
    assert result.exit_code == 0, result.output
    assert pulled == ["bob/Foo"]  # distinct model, not aliased to alice/Foo


def test_pull_single_surfaces_copy_failure_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    # A real copy/download failure must be a clean user-facing error, not a traceback.
    def boom(*_a: object, **_k: object) -> PullResult:
        raise OSError("disk full")

    monkeypatch.setattr(catalog_ops, "pull", boom)
    result = runner.invoke(cli.app, ["library", "pull", "ollama", "some:model"])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)  # handled via Exit(), not an uncaught OSError
    assert "disk full" in result.output


def test_chat_refuses_ollama_model_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    ollama_model = _lib_model("gemma4:31b")
    ollama_model.is_ollama = True
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [ollama_model])
    result = runner.invoke(cli.app, ["chat", "gemma4:31b"])
    assert result.exit_code == 1
    assert "Ollama model" in result.output
    assert "ollama run" in result.output


def test_sources_hides_non_chat_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library_ops, "scan", lambda: [])
    monkeypatch.setattr(
        catalog_ops,
        "list_models",
        lambda *a, **k: Catalog(
            entries=[
                _entry("pub/Chat-GGUF", generative=True, fmt=ModelFormat.gguf),
                _entry("st/all-MiniLM", generative=False, fmt=ModelFormat.safetensors),
            ]
        ),
    )
    default = runner.invoke(cli.app, ["library", "sources"])
    assert "pub/Chat-GGUF" in default.output
    assert "all-MiniLM" not in default.output  # embedding hidden by default
    assert "hidden" in default.output

    everything = runner.invoke(cli.app, ["library", "sources", "--all"])
    assert "all-MiniLM" in everything.output  # shown with --all


def test_split_input_images_detects_dropped_paths(tmp_path: Path) -> None:
    # A terminal drag-drop inserts the file path as text; the REPL should peel it
    # out and attach it, leaving the remaining words as the message.
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    text, images, _, _ = attach.split_input_media(f"what is this {img}")
    assert text == "what is this"
    assert len(images) == 1 and images[0].startswith("data:image/png;base64,")


def test_split_input_images_leaves_plain_text(tmp_path: Path) -> None:
    text, images, _, _ = attach.split_input_media("just a normal message, no path")
    assert text == "just a normal message, no path"
    assert images == []


def test_split_input_images_handles_escaped_spaces(tmp_path: Path) -> None:
    # Terminal drag-drop escapes spaces with a backslash; shlex unescapes them.
    img = tmp_path / "my pic.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    escaped = str(img).replace(" ", "\\ ")
    text, images, _, _ = attach.split_input_media(f"describe {escaped}")
    assert text == "describe"
    assert len(images) == 1 and images[0].startswith("data:image/jpeg;base64,")


def test_split_input_detects_dropped_text_files(tmp_path: Path) -> None:
    # A dragged text/code file is read as (name, contents) for prompt inlining.
    doc = tmp_path / "notes.py"
    doc.write_text("print('hi')\n")
    text, images, audios, files = attach.split_input_media(f"explain {doc}")
    assert text == "explain"
    assert images == [] and audios == []
    assert files == [("notes.py", "print('hi')\n")]


def test_inline_files_prepends_fenced_blocks() -> None:
    inlined = attach.inline_files("summarize", [("a.txt", "hello"), ("b.md", "# world")])
    assert "Attached file: a.txt\n```\nhello\n```" in inlined
    assert "Attached file: b.md\n```\n# world\n```" in inlined
    assert inlined.endswith("summarize")
    # No text: just the blocks.
    assert attach.inline_files("", [("a.txt", "hi")]) == "Attached file: a.txt\n```\nhi\n```"
