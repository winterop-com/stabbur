"""CLI behavior tests (Typer CliRunner) — library-centric list + chat-only guard."""

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stabbur import attach, cli
from stabbur import catalog as catalog_ops
from stabbur import library as library_ops
from stabbur.library import LibraryModel
from stabbur.models import Catalog, ModelEntry, ModelFormat, ModelSource, PullResult

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


_GEN_CONFIG = b'{"architectures": ["LlamaForCausalLM"]}'  # marks a safetensors dir as a generative chat LLM


def _mk_lib_dir(path: Path, *files: tuple[str, bytes]) -> None:
    """Create a model directory in a synthetic library with the given (name, content) files."""
    path.mkdir(parents=True, exist_ok=True)
    for name, content in files:
        (path / name).write_bytes(content)


def test_library_formats_flags_redundant_and_missing_quant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur import library

    # gguf-only (no note); gguf + safetensors (safetensors redundant); safetensors-only (no quant).
    _mk_lib_dir(tmp_path / "gguf" / "pub" / "OnlyGGUF", ("model.Q4_K_M.gguf", b"g" * 100))
    _mk_lib_dir(tmp_path / "gguf" / "pub" / "Both", ("model.Q4_K_M.gguf", b"g" * 100))
    _mk_lib_dir(
        tmp_path / "safetensors" / "pub" / "Both",
        ("model.safetensors", b"s" * 500),
        ("config.json", _GEN_CONFIG),
    )
    _mk_lib_dir(
        tmp_path / "safetensors" / "pub" / "OnlySafe",
        ("model.safetensors", b"s" * 300),
        ("config.json", _GEN_CONFIG),
    )

    real_scan = library.scan  # capture before patching (library_ops and library are one module)
    monkeypatch.setattr(library_ops, "scan", lambda: real_scan(root=tmp_path))
    result = runner.invoke(cli.app, ["library", "formats"])
    assert result.exit_code == 0, result.output
    out = result.output

    # The redundant safetensors copy whose reclaimable size the footer totals (OnlySafe is NOT
    # redundant — it has no quant — so it must not be counted).
    both_sft = next(
        m for m in real_scan(root=tmp_path) if m.name == "pub/Both" and m.model_format is ModelFormat.safetensors
    )

    assert "pub/OnlyGGUF" in out  # gguf-only model is listed...
    assert out.count("redundant safetensors (") == 1  # ...but flagged for exactly one model (Both)
    assert out.count("no ready-to-run quant") == 1  # exactly one safetensors-only model (OnlySafe)
    assert "stabbur library rm pub/Both --format safetensors" in out  # actionable hint
    assert f"{both_sft.size_human} reclaimable" in out  # total == only the redundant copy's size


def test_library_formats_empty_library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library_ops, "scan", lambda: [])
    result = runner.invoke(cli.app, ["library", "formats"])
    assert result.exit_code == 0, result.output
    assert "No chat models" in result.output


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


def test_init_writes_manifest_and_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import tomllib

    # A configured shared library holding the model → init uses it (no pull, no local store).
    monkeypatch.setattr(library_ops, "configured", lambda *a, **k: True)
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("unsloth/X-GGUF")])
    monkeypatch.setattr(cli.project, "_pick_tools_interactive", lambda: [])
    monkeypatch.chdir(tmp_path)
    # input = blank lines accepting the defaults for the kind + system-prompt questions
    first = runner.invoke(cli.app, ["project", "init", "--model", "unsloth/X-GGUF"], input="\n\n")
    assert first.exit_code == 0, first.output
    parsed = tomllib.loads(Path("stabbur.toml").read_text())
    assert parsed["project"]["model"] == "unsloth/X-GGUF"
    assert "libraries" not in parsed  # uses the shared library — no project-local store

    again = runner.invoke(cli.app, ["project", "init", "--model", "unsloth/X-GGUF"], input="\n")
    assert again.exit_code == 1  # refuses to clobber an existing project
    assert "already exists" in again.output


def test_pick_model_rejects_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer

    # "0" used to negative-index to the last option (a surprise selection); it must be rejected
    # like any out-of-range choice. No library configured → options are just the curated starters.
    monkeypatch.setattr(library_ops, "configured", lambda *a, **k: False)
    monkeypatch.setattr(cli.project.typer, "prompt", lambda *a, **k: "0")
    with pytest.raises(typer.Exit) as exc:
        cli.project._pick_model_interactive()
    assert exc.value.exit_code == 1


def test_project_new_cancel_leaves_no_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import typer

    # Canceling the wizard mid-prompt must not leave an empty project directory behind.
    def _abort() -> str:
        raise typer.Abort

    monkeypatch.setattr(cli.project, "_pick_model_interactive", _abort)
    monkeypatch.chdir(tmp_path)
    # answer the kind question, then the (mocked) model step aborts
    result = runner.invoke(cli.app, ["project", "new", "hello"], input="1\n")
    assert result.exit_code != 0
    assert not Path("hello").exists()  # nothing created on cancel


def test_project_show_lists_model_prompt_and_live_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    # `project show` must surface the bound model, the system prompt, and the *actual*
    # tools (from connecting to the MCP servers) — not just server names.
    from stabbur import mcpservers
    from stabbur import project as project_mod

    proj = project_mod.Project(model="unsloth/X-GGUF", system_prompt="Be concise.")
    monkeypatch.setattr(project_mod, "load", lambda *a, **k: proj)
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("unsloth/X-GGUF")])
    # Tools now come from the resolved mcp.json layers; stub resolve + the connect so it's hermetic.
    monkeypatch.setattr(
        mcpservers, "resolve", lambda *a, **k: [mcpservers.McpServer(name="datetime", command="stabbur-mcp-datetime")]
    )
    monkeypatch.setattr(
        cli.project, "_connect_project_tools", lambda mcp: ({"datetime": [("today", "Return today's date.")]}, None, [])
    )
    result = runner.invoke(cli.app, ["project", "show"])
    assert result.exit_code == 0, result.output
    assert "unsloth/X-GGUF" in result.output
    assert "Be concise." in result.output
    assert "today" in result.output  # the real tool name, not just "datetime"


def test_project_show_from_a_subdirectory_names_the_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Run from a subdirectory, `show` binds to the project found above — and says which one, since
    # "Project (stabbur.toml)" would name a file that isn't in this directory at all.
    from stabbur import mcpservers

    (tmp_path / "stabbur.toml").write_text('[project]\nmodel = "unsloth/X-GGUF"\nsystem_prompt = "Be concise."\n')
    sub = tmp_path / "src"
    sub.mkdir()
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("unsloth/X-GGUF")])
    monkeypatch.setattr(mcpservers, "resolve", lambda *a, **k: [])  # hermetic: no MCP servers to spawn
    monkeypatch.chdir(sub)
    result = runner.invoke(cli.app, ["project", "show"])
    assert result.exit_code == 0, result.output
    assert str(tmp_path / "stabbur.toml") in result.output
    assert "Be concise." in result.output  # it really loaded the parent's project


def test_project_init_warns_when_it_nests_inside_a_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `init` scaffolds where you stand, always — but inside an existing project that shadows the
    # outer assistant from here down, which is worth a word rather than a silent surprise.
    monkeypatch.setattr(library_ops, "configured", lambda *a, **k: True)
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("unsloth/X-GGUF")])
    monkeypatch.setattr(cli.project, "_pick_tools_interactive", lambda: [])
    (tmp_path / "stabbur.toml").write_text('[project]\nmodel = "unsloth/X-GGUF"\n')
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)

    result = runner.invoke(cli.app, ["project", "init", "--model", "unsloth/X-GGUF"], input="\n\n")
    assert result.exit_code == 0, result.output
    assert "inside an existing project" in result.output
    assert (sub / "stabbur.toml").is_file()  # scaffolded here, not redirected up to the outer project


def test_project_show_without_manifest_hints_init(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur import project as project_mod

    monkeypatch.setattr(project_mod, "load", lambda *a, **k: None)
    result = runner.invoke(cli.app, ["project", "show"])
    assert result.exit_code == 1
    assert "stabbur project init" in result.output


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


def _sized_entry(name: str, size_bytes: int, fmt: ModelFormat = ModelFormat.gguf) -> ModelEntry:
    return ModelEntry(
        source=ModelSource.lmstudio, name=name, model_format=fmt, path=Path("/tmp") / name, size_bytes=size_bytes
    )


def test_sources_marks_a_different_quant_apart_from_a_match(monkeypatch: pytest.MonkeyPatch) -> None:
    # The IN LIBRARY tick matched on name alone, so a 17.5 GB source copy was ticked against the
    # library's 26.4 GB copy of the same repo — a quant the library does not have.
    in_library = _lib_model("pub/Repo-GGUF")
    in_library.size_bytes = 26_400_000_000
    same = _lib_model("pub/Same-GGUF")
    same.size_bytes = 4_000_000_000
    monkeypatch.setattr(library_ops, "scan", lambda: [in_library, same])
    monkeypatch.setattr(
        catalog_ops,
        "list_models",
        lambda *a, **k: Catalog(
            entries=[
                _sized_entry("pub/Repo-GGUF", 17_500_000_000),
                _sized_entry("pub/Same-GGUF", 4_000_000_000),
                _sized_entry("pub/Absent-GGUF", 1_000_000_000),
            ]
        ),
    )
    result = runner.invoke(cli.app, ["library", "sources"])
    assert result.exit_code == 0, result.output
    assert "other quant" in result.output
    assert "1 already in your library" in result.output  # only the size-compatible copy counts
    assert "1 a different quant/format" in result.output


def test_sources_marks_a_different_format_apart_from_a_match(monkeypatch: pytest.MonkeyPatch) -> None:
    mlx_copy = _lib_model("pub/Repo", fmt=ModelFormat.mlx)
    mlx_copy.size_bytes = 4_000_000_000
    monkeypatch.setattr(library_ops, "scan", lambda: [mlx_copy])
    monkeypatch.setattr(
        catalog_ops,
        "list_models",
        lambda *a, **k: Catalog(entries=[_sized_entry("pub/Repo", 4_000_000_000, ModelFormat.gguf)]),
    )
    result = runner.invoke(cli.app, ["library", "sources"])
    assert "other format" in result.output


def test_installed_finds_an_ollama_install_made_with_a_custom_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `install --to ollama --name custom-name` succeeded but `library installed` showed nothing,
    # so there was no way to discover what to uninstall.
    from stabbur import consumers

    model_dir = tmp_path / "gguf" / "pub" / "Repo-GGUF"
    model_dir.mkdir(parents=True)
    (model_dir / "w.gguf").write_bytes(b"w")
    model = LibraryModel(
        name="pub/Repo-GGUF",
        model_format=ModelFormat.gguf,
        path=model_dir,
        load_target=model_dir / "w.gguf",
        library_root=tmp_path,
    )
    consumers.record_install(model, "ollama", "custom-name")

    monkeypatch.setattr(library_ops, "scan", lambda: [model])
    monkeypatch.setattr(library_ops, "roots", lambda *a, **k: [tmp_path])
    monkeypatch.setattr(consumers, "ollama_installed_names", lambda: {"custom-name"})
    monkeypatch.setattr(consumers, "lmstudio_linked_names", lambda roots: set())

    result = runner.invoke(cli.app, ["library", "installed"])
    assert result.exit_code == 0, result.output
    assert "pub/Repo-GGUF" in result.output
    assert "custom-name" in result.output


def test_uninstall_ollama_prefers_a_recorded_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur import consumers

    model_dir = tmp_path / "gguf" / "pub" / "Repo-GGUF"
    model_dir.mkdir(parents=True)
    (model_dir / "w.gguf").write_bytes(b"w")
    model = LibraryModel(
        name="pub/Repo-GGUF",
        model_format=ModelFormat.gguf,
        path=model_dir,
        load_target=model_dir / "w.gguf",
        library_root=tmp_path,
    )
    consumers.record_install(model, "ollama", "custom-name")
    removed: list[str] = []

    def _rm(name: str) -> consumers.InstallResult:
        removed.append(name)
        return consumers.InstallResult(runtime="ollama", name=name, detail="removed from Ollama")

    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [model])
    monkeypatch.setattr(consumers, "ollama_installed_names", lambda: {"custom-name"})
    monkeypatch.setattr(consumers, "uninstall_ollama", _rm)

    result = runner.invoke(cli.app, ["library", "uninstall", "pub/Repo-GGUF", "--from", "ollama"])
    assert result.exit_code == 0, result.output
    assert removed == ["custom-name"]  # not the derived "repo", which Ollama never held
    assert consumers.recorded_install_names(model, "ollama") == []  # and the record is cleared


def test_pull_move_distinguishes_already_present_from_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    # source_removed=False alone can't tell "nothing was copied" from "the copy failed to verify";
    # reporting the second for the first warned the user about a model that was simply already there.
    def already_there(*_a: object, **_k: object) -> PullResult:
        return PullResult(source=ModelSource.voice, name="kokoro", destination=Path("/tmp/lib"), already_present=True)

    monkeypatch.setattr(catalog_ops, "pull", already_there)
    result = runner.invoke(cli.app, ["library", "pull", "voice", "kokoro", "--move"])
    assert result.exit_code == 0, result.output
    assert "already in the library" in result.output
    assert "could not be verified" not in result.output

    def unverified(*_a: object, **_k: object) -> PullResult:
        return PullResult(source=ModelSource.voice, name="kokoro", destination=Path("/tmp/lib"))

    monkeypatch.setattr(catalog_ops, "pull", unverified)
    kept = runner.invoke(cli.app, ["library", "pull", "voice", "kokoro", "--move"])
    assert "could not be verified" in kept.output


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


def test_uninstalled_optional_lists_web_only_when_absent() -> None:
    # `web` is optional (behind `--extra web`); it's listed for discovery only when not installed.
    assert [o.name for o in cli._uninstalled_optional(set())] == ["web"]
    assert cli._uninstalled_optional({"web"}) == []  # installed/advertised -> not in the optional list


def test_mcp_list_shows_optional_web_with_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur import plugins

    monkeypatch.setattr(plugins, "advertised_servers", lambda _pm: [])  # simulate web (and all) not installed
    result = runner.invoke(cli.app, ["mcp", "list"])
    assert result.exit_code == 0
    assert "web" in result.stdout and "install-web" in result.stdout  # discoverable with a hint


def test_mcp_add_hint_matches_where_you_are(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Outside a project the old hint pointed at `project show`, which only answers "No stabbur.toml here."
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)
    bare = runner.invoke(cli.app, ["mcp", "add", "datetime"])
    assert bare.exit_code == 0, bare.output
    assert "stabbur mcp list" in bare.output and "project show" not in bare.output
    # With a stabbur.toml present, `project show` is the right place to look again.
    (tmp_path / "stabbur.toml").write_text("[project]\n")
    (tmp_path / ".mcp.json").unlink()
    in_project = runner.invoke(cli.app, ["mcp", "add", "datetime"])
    assert in_project.exit_code == 0, in_project.output
    assert "stabbur project show" in in_project.output


def test_mcp_add_writes_into_the_discovered_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # From a subdirectory, `mcp add` extends *this* project's toolset instead of dropping a second
    # .mcp.json where you happen to be standing — which the project would never read.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "stabbur.toml").write_text("[project]\n")
    sub = tmp_path / "src" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)

    result = runner.invoke(cli.app, ["mcp", "add", "datetime"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".mcp.json").is_file()
    assert not (sub / ".mcp.json").exists()
    assert "stabbur project show" in result.output  # a project is in scope, so that hint applies


def test_voice_import_rejects_all_with_ids() -> None:
    # C-9: --all combined with explicit ids is a contradiction (like `library pull`) → exit 2.
    result = runner.invoke(cli.app, ["voice", "import", "--all", "kokoro"])
    assert result.exit_code == 2, result.output
    assert "OR --all" in result.output


def test_config_set_get_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("STABBUR_DEFAULT_MODEL", raising=False)
    set_res = runner.invoke(cli.app, ["config", "set", "model", "pub/Model-GGUF"])
    assert set_res.exit_code == 0, set_res.output
    assert "Set model = pub/Model-GGUF" in set_res.output
    from stabbur import userconfig

    assert userconfig.read()["default_model"] == "pub/Model-GGUF"
    # `get` prints just the raw value (scriptable); `list` shows the whole picture.
    got = runner.invoke(cli.app, ["config", "get", "model"])
    assert got.exit_code == 0 and got.output.strip() == "pub/Model-GGUF"
    listed = runner.invoke(cli.app, ["config", "list"])
    assert listed.exit_code == 0 and "pub/Model-GGUF" in listed.output


def test_config_get_unset_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("STABBUR_CHAT_SERVER", raising=False)
    got = runner.invoke(cli.app, ["config", "get", "server"])
    assert got.exit_code == 0 and got.output.strip() == ""


def test_config_get_set_reject_unknown_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for args in (["config", "set", "bogus", "x"], ["config", "get", "bogus"]):
        res = runner.invoke(cli.app, args)
        assert res.exit_code == 1 and "Unknown key" in res.output


def test_setup_persists_defaults_non_interactive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("STABBUR_LIBRARY_ROOT", raising=False)
    monkeypatch.setattr(library_ops, "scan", lambda *a, **k: [])  # empty library (find() passes args through)
    lib = tmp_path / "lib"
    res = runner.invoke(cli.app, ["setup", "--yes", "--library-root", str(lib), "--model", "pub/M", "--no-build-ui"])
    assert res.exit_code == 0, res.output
    from stabbur import userconfig

    stored = userconfig.read()
    assert stored["default_model"] == "pub/M"
    assert Path(stored["library_root"]) == lib.resolve()
    assert lib.is_dir()  # setup created it


def _fake_extension_checkout(root: Path) -> Path:
    """Create the minimal marker `stabbur ext-dev` discovery looks for: extension/wxt.config.ts."""
    ext = root / "extension"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / "wxt.config.ts").write_text("// marker\n")
    return ext


def test_ext_dev_outside_checkout_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no extension/wxt.config.ts anywhere above
    res = runner.invoke(cli.app, ["ext-dev"])
    assert res.exit_code == 1
    assert "stabbur source checkout" in res.output


def test_ext_dev_requires_bun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_extension_checkout(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: None)  # bun absent
    res = runner.invoke(cli.app, ["ext-dev"])
    assert res.exit_code == 1
    assert "bun not found" in res.output


def test_ext_dev_requires_installed_deps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_extension_checkout(tmp_path)  # but no node_modules
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/bun")
    res = runner.invoke(cli.app, ["ext-dev"])
    assert res.exit_code == 1
    assert "deps not installed" in res.output


def test_ext_dev_discovers_root_from_subdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Discovery walks up: invoked from a nested dir, it still finds the checkout — but with bun
    # absent it stops at the precondition (never launching a browser), which is what we assert.
    _fake_extension_checkout(tmp_path)
    nested = tmp_path / "src" / "stabbur"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    res = runner.invoke(cli.app, ["ext-dev"])
    assert res.exit_code == 1
    assert "bun not found" in res.output  # got past discovery, failed on the precondition


_BIND_HOSTS = [
    # (bind host, is it reachable only from this machine?)
    ("127.0.0.1", True),
    ("localhost", True),
    ("::1", True),
    ("[::1]", True),
    ("127.0.0.5", True),  # the whole 127.0.0.0/8 loopback range, not just the one address
    ("", False),  # INADDR_ANY: uvicorn binds every interface, exactly like 0.0.0.0
    ("0.0.0.0", False),  # noqa: S104 - the point of the test
    ("::", False),
    ("lab-rig.example", False),  # a name we cannot prove is this machine → treat as exposed
    ("192.0.2.10", False),
]


@pytest.mark.parametrize(("bind_host", "is_loopback"), _BIND_HOSTS)
def test_bind_host_classification(bind_host: str, is_loopback: bool) -> None:
    from stabbur import config

    assert config.is_loopback_bind(bind_host) is is_loopback


@pytest.mark.parametrize(("bind_host", "is_loopback"), _BIND_HOSTS)
def test_exposed_bind_always_gets_a_token(bind_host: str, is_loopback: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    # The consequence of the classification above: any bind reachable from outside this machine
    # must come up with a bearer token, or model control and MCP tool execution (arbitrary code,
    # via the exec server) are open to the LAN. The host arrives via STABBUR_HOST because that —
    # not --host — is the channel an any-address bind can come through (`--host ""` is falsy and
    # falls back to the configured default).
    import uvicorn

    from stabbur.config import get_settings

    monkeypatch.setenv("STABBUR_AUTH_TOKEN", "")  # restored on teardown, whatever serve() writes
    monkeypatch.setenv("STABBUR_HOST", bind_host)
    monkeypatch.setattr("stabbur.cli.serve._port_free", lambda _h, _p: True)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    get_settings.cache_clear()
    try:
        result = runner.invoke(cli.app, ["serve"])
        assert result.exit_code == 0, result.output
        token = os.environ.get("STABBUR_AUTH_TOKEN", "")
        assert bool(token) is not is_loopback
        assert ("Exposed on" in result.output) is not is_loopback
        assert ("bearer token required" in result.output) is not is_loopback
        # The printed URL must be openable: an empty host would otherwise read as `http://:2222`.
        assert "http://:" not in result.output
    finally:
        get_settings.cache_clear()


def test_normalize_server_url() -> None:
    assert cli._normalize_server_url("http://h:8000") == "http://h:8000"
    assert cli._normalize_server_url("http://h:8000/") == "http://h:8000"
    assert cli._normalize_server_url("http://h:8000/v1") == "http://h:8000"
    assert cli._normalize_server_url("  http://h:8000/v1/  ") == "http://h:8000"
    assert cli._normalize_server_url(None) is None
    assert cli._normalize_server_url("  ") is None


def test_runtime_generate_attaches_without_spawning(monkeypatch: pytest.MonkeyPatch) -> None:
    # With base_url set, generate() must NOT spawn a runtime (_serve); it POSTs to the given base.
    from stabbur import runtime

    def _boom(_model: object) -> object:
        raise AssertionError("_serve must not be called when base_url is provided")

    seen: dict[str, object] = {}

    def _fake_chat(
        base: str, model: object, messages: object, max_tokens: object = None, model_id: object = None
    ) -> str:
        seen["base"] = base
        return "attached reply"

    monkeypatch.setattr(runtime, "_serve", _boom)
    monkeypatch.setattr(runtime, "_chat", _fake_chat)
    out = runtime.generate(_lib_model("pub/X"), "hi", base_url="http://127.0.0.1:8000")
    assert out == "attached reply"
    assert seen["base"] == "http://127.0.0.1:8000"


def test_chat_p_server_flag_passes_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur import capabilities, runtime

    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("pub/X")])
    monkeypatch.setattr(capabilities, "capabilities", lambda _m: capabilities.ModelCapabilities())
    captured: dict[str, object] = {}

    def _fake_generate(model: object, prompt: str, *a: object) -> str:
        captured["base_url"] = a[4]  # (max_tokens, system_prompt, images, audios, base_url, model_id)
        captured["model_id"] = a[5]
        return "ok"

    monkeypatch.setattr(runtime, "generate", _fake_generate)
    # The wire model id always comes from the remote's own listing (a local path would match
    # nothing there), so the remote path probes /v1/models — stub it rather than hitting it.
    monkeypatch.setattr(cli.chat, "_probe_json", lambda url: {"data": [{"id": "pub/X"}]})
    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools", "--server", "http://127.0.0.1:8000/v1"])
    assert result.exit_code == 0, result.output
    assert captured["base_url"] == "http://127.0.0.1:8000"  # normalized (trailing /v1 stripped)
    assert captured["model_id"] == "pub/X"  # the remote's id, never the local load_target path


def _stub_remote_listing(monkeypatch: pytest.MonkeyPatch, *rows: tuple[str, bool]) -> None:
    """Answer ``GET /v1/models`` with ``(id, loaded)`` rows (the remote-attach discovery probe)."""
    listing = {"data": [{"id": rid, **({"status": {"value": "loaded"}} if loaded else {})} for rid, loaded in rows]}
    monkeypatch.setattr(cli.chat, "_probe_json", lambda _url: listing)


def test_chat_p_server_does_not_demand_the_machine_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # A machine default (`stabbur config set model`) says what to LOAD when nothing else does — it is
    # not a request. Substituted before the remote's listing was matched, it made every
    # `chat -p --server` fail with "does not serve <default>" on a box holding other models, and
    # contradicted the documented "with no name, the remote's loaded model wins".
    from stabbur import runtime

    # Stand in for `stabbur config set model` (Settings is process-cached, so the env var alone
    # would not reach this run): resolve_model's real precedence with a machine default set.
    monkeypatch.setattr(
        cli.chat.project,
        "resolve_model",
        lambda explicit, proj: explicit or (proj.model if proj else None) or "pub/Only-Here",
    )
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [])
    _stub_remote_listing(monkeypatch, ("remote-a", False), ("remote-b", True))
    captured: dict[str, object] = {}
    monkeypatch.setattr(runtime, "generate", lambda model, prompt, *a: captured.setdefault("model_id", a[5]) or "ok")

    result = runner.invoke(cli.app, ["chat", "-p", "hi", "--no-tools", "--server", "http://127.0.0.1:8000"])
    assert result.exit_code == 0, result.output
    assert captured["model_id"] == "remote-b"  # the model the remote has loaded


def test_chat_p_server_still_refuses_an_explicitly_named_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # An explicit name is a request, and a request the server can't serve must fail loudly.
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [])
    _stub_remote_listing(monkeypatch, ("remote-a", True))

    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools", "--server", "http://127.0.0.1:8000"])
    assert result.exit_code == 1
    assert "does not serve" in result.output and "remote-a" in result.output  # and says what it does serve


def test_chat_p_server_still_refuses_a_project_model_it_cannot_serve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The project's model is the project speaking, not a per-machine fallback: keep failing loudly.
    monkeypatch.chdir(tmp_path)
    Path("stabbur.toml").write_text('[project]\nmodel = "proj/Bound-GGUF"\n', encoding="utf-8")
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [])
    _stub_remote_listing(monkeypatch, ("remote-a", True))

    result = runner.invoke(cli.app, ["chat", "-p", "hi", "--no-tools", "--server", "http://127.0.0.1:8000"])
    assert result.exit_code == 1
    assert "does not serve 'proj/Bound-GGUF'" in result.output


def test_chat_p_auto_attaches_to_running_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur import capabilities, runtime
    from stabbur.runtime import serve_registry
    from stabbur.runtime.serve_registry import ServeRecord

    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("pub/X")])
    monkeypatch.setattr(capabilities, "capabilities", lambda _m: capabilities.ModelCapabilities())
    # No --server/config, but a live serve is registered for this model -> auto-attach.
    monkeypatch.setattr(
        serve_registry, "discover", lambda name: ServeRecord(base_url="http://127.0.0.1:9", model=name, pid=1)
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(runtime, "generate", lambda model, prompt, *a: captured.setdefault("base_url", a[4]) or "ok")
    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools"])
    assert result.exit_code == 0, result.output
    assert captured["base_url"] == "http://127.0.0.1:9"
    assert "attaching to running stabbur serve" in result.output  # note printed


def test_chat_p_no_serve_spawns_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur import capabilities, runtime
    from stabbur.runtime import serve_registry

    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("pub/X")])
    monkeypatch.setattr(capabilities, "capabilities", lambda _m: capabilities.ModelCapabilities())
    monkeypatch.setattr(serve_registry, "discover", lambda _name: None)  # nothing running
    captured: dict[str, object] = {}
    monkeypatch.setattr(runtime, "generate", lambda model, prompt, *a: captured.setdefault("base_url", a[4]) or "ok")
    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools"])
    assert result.exit_code == 0, result.output
    assert captured["base_url"] is None  # falls back to spawning a local runtime


def test_chat_no_server_overrides_a_configured_server(monkeypatch: pytest.MonkeyPatch) -> None:
    # A configured chat server otherwise applies to every run with no way back to a local load.
    from stabbur import capabilities, runtime
    from stabbur.runtime import serve_registry

    monkeypatch.setenv("STABBUR_CHAT_SERVER", "http://gpu-box:8080")
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("pub/X")])
    monkeypatch.setattr(capabilities, "capabilities", lambda _m: capabilities.ModelCapabilities())
    monkeypatch.setattr(serve_registry, "discover", lambda _name: None)
    captured: dict[str, object] = {}
    monkeypatch.setattr(runtime, "generate", lambda model, prompt, *a: captured.setdefault("base_url", a[4]) or "ok")

    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools", "--no-server"])
    assert result.exit_code == 0, result.output
    assert captured["base_url"] is None  # local runtime, not the configured server


def test_chat_no_server_also_skips_auto_attach(monkeypatch: pytest.MonkeyPatch) -> None:
    # Attaching to a running serve is still not a local load, so --no-server opts out of that too.
    from stabbur import capabilities, runtime
    from stabbur.runtime import serve_registry
    from stabbur.runtime.serve_registry import ServeRecord

    monkeypatch.delenv("STABBUR_CHAT_SERVER", raising=False)
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("pub/X")])
    monkeypatch.setattr(capabilities, "capabilities", lambda _m: capabilities.ModelCapabilities())
    monkeypatch.setattr(
        serve_registry, "discover", lambda name: ServeRecord(base_url="http://127.0.0.1:9", model=name, pid=1)
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(runtime, "generate", lambda model, prompt, *a: captured.setdefault("base_url", a[4]) or "ok")

    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools", "--no-server"])
    assert result.exit_code == 0, result.output
    assert captured["base_url"] is None
    assert "attaching to running stabbur serve" not in result.output


def test_chat_server_and_no_server_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("pub/X")])
    result = runner.invoke(
        cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools", "--server", "http://x:1", "--no-server"]
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_chat_empty_server_string_clears_a_configured_server(monkeypatch: pytest.MonkeyPatch) -> None:
    # `--server ''` reads as "no server"; it used to fall back to the configured one instead.
    from stabbur import capabilities, runtime
    from stabbur.runtime import serve_registry

    monkeypatch.setenv("STABBUR_CHAT_SERVER", "http://gpu-box:8080")
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("pub/X")])
    monkeypatch.setattr(capabilities, "capabilities", lambda _m: capabilities.ModelCapabilities())
    monkeypatch.setattr(serve_registry, "discover", lambda _name: None)
    captured: dict[str, object] = {}
    monkeypatch.setattr(runtime, "generate", lambda model, prompt, *a: captured.setdefault("base_url", a[4]) or "ok")

    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools", "--server", ""])
    assert result.exit_code == 0, result.output
    assert captured["base_url"] is None


def _stub_httpx_get(monkeypatch: pytest.MonkeyPatch, responses: dict[str, object]) -> None:
    """Fake ``httpx.get`` by URL suffix: a payload answers with it; a missing suffix refuses."""
    from types import SimpleNamespace

    import httpx

    def _get(url: str, timeout: object = None) -> object:
        for suffix, payload in responses.items():
            if url.endswith(suffix):
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda payload=payload: payload)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", _get)


def test_chat_tui_server_flag_attaches_without_spawning(monkeypatch: pytest.MonkeyPatch) -> None:
    # --server with no -p now attaches the interactive TUI: no runtime spawn, remote endpoint.
    from stabbur import capabilities, chat_tui, runtime

    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("pub/X")])
    monkeypatch.setattr(capabilities, "capabilities", lambda _m: capabilities.ModelCapabilities())
    monkeypatch.setattr(runtime, "load", lambda _m: (_ for _ in ()).throw(AssertionError("must not spawn")))
    _stub_httpx_get(monkeypatch, {"/api/status": {"state": "running", "model": "pub/X", "n_ctx": 2048}})
    captured: dict[str, object] = {}
    monkeypatch.setattr(chat_tui, "run_interactive", lambda **kw: captured.update(kw))

    result = runner.invoke(cli.app, ["chat", "pub/X", "--no-tools", "--server", "http://127.0.0.1:8000/v1"])
    assert result.exit_code == 0, result.output
    endpoint = captured["endpoint"]
    assert isinstance(endpoint, chat_tui.RemoteEndpoint)
    assert endpoint.base == "http://127.0.0.1:8000"  # normalized (trailing /v1 stripped)
    assert endpoint.model is not None and endpoint.model.name == "pub/X"  # local metadata kept
    assert endpoint.n_ctx == 2048


def test_chat_tui_server_attaches_without_local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # The served model doesn't exist locally (and no name was given): attach on server metadata alone.
    from stabbur import chat_tui, runtime

    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [])
    monkeypatch.setattr(runtime, "load", lambda _m: (_ for _ in ()).throw(AssertionError("must not spawn")))
    _stub_httpx_get(monkeypatch, {"/api/status": {"state": "running", "model": "pub/Served-GGUF"}})
    captured: dict[str, object] = {}
    monkeypatch.setattr(chat_tui, "run_interactive", lambda **kw: captured.update(kw))

    result = runner.invoke(cli.app, ["chat", "--no-tools", "--server", "http://127.0.0.1:8000"])
    assert result.exit_code == 0, result.output
    endpoint = captured["endpoint"]
    assert isinstance(endpoint, chat_tui.RemoteEndpoint)
    assert endpoint.model is None
    assert endpoint.model_name == "pub/Served-GGUF"  # discovered from /api/status


def test_probe_remote_falls_back_to_v1_models(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not stabbur serve (no /api/status): a raw llama-server answers /v1/models.
    from stabbur.cli.chat import _probe_remote

    _stub_httpx_get(monkeypatch, {"/v1/models": {"data": [{"id": "/models/foo.gguf"}]}})
    endpoint = _probe_remote("http://127.0.0.1:9999", None, None)
    assert endpoint.model_id == "/models/foo.gguf"  # sent as the OpenAI model field
    assert endpoint.model_name == "/models/foo.gguf"


def test_probe_remote_reads_the_context_window_from_v1_models(monkeypatch: pytest.MonkeyPatch) -> None:
    # The footer's context gauge simply vanished against a plain llama-server: n_ctx was read only
    # from stabbur serve's /api/status, while the /v1/models row already carries the loaded window.
    from stabbur.cli.chat import _probe_remote

    _stub_httpx_get(
        monkeypatch,
        {
            "/v1/models": {
                "data": [
                    {"id": "other", "meta": {"n_ctx": 512}},
                    {"id": "running", "status": {"value": "loaded"}, "meta": {"n_ctx": 8192, "n_ctx_train": 262144}},
                ]
            }
        },
    )
    endpoint = _probe_remote("http://127.0.0.1:9999", None, None)
    assert endpoint.model_id == "running"  # the loaded model, not the first listed
    assert endpoint.n_ctx == 8192  # its window — not n_ctx_train, which is the model's maximum


def test_probe_remote_survives_a_server_that_reports_no_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur.cli.chat import _probe_remote

    _stub_httpx_get(monkeypatch, {"/v1/models": {"data": [{"id": "plain", "meta": {"n_ctx_train": 4096}}]}})
    assert _probe_remote("http://127.0.0.1:9999", None, None).n_ctx is None  # gauge omitted, as before


def test_probe_remote_exits_when_nothing_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer

    from stabbur.cli.chat import _probe_remote

    _stub_httpx_get(monkeypatch, {})  # both probes refuse
    with pytest.raises(typer.Exit):
        _probe_remote("http://127.0.0.1:1", None, None)


def test_probe_remote_exits_when_serve_has_no_model_and_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer

    from stabbur.cli.chat import _probe_remote

    # Unlocked, empty, and nothing to auto-load (no request, no server default) -> exit with a hint.
    _stub_httpx_get(monkeypatch, {"/api/status": {"state": "stopped", "model": None, "locked": False}})
    with pytest.raises(typer.Exit):
        _probe_remote("http://127.0.0.1:8000", None, None)


def test_probe_remote_exits_when_locked_serve_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer

    from stabbur.cli.chat import _probe_remote

    # A locked serve loads eagerly; empty means its load failed -> never try to load into it.
    _stub_httpx_get(monkeypatch, {"/api/status": {"state": "stopped", "model": None, "locked": True, "error": "boom"}})
    with pytest.raises(typer.Exit):
        _probe_remote("http://127.0.0.1:8000", None, None)


def _stub_loadable_serve(monkeypatch: pytest.MonkeyPatch, default: str | None) -> list[str]:
    """A fake idle unlocked serve: POST /api/load/<name> flips /api/status to ready on <name>."""
    from types import SimpleNamespace

    import httpx

    posts: list[str] = []
    loaded: dict[str, str | None] = {"model": None}

    def _get(url: str, timeout: object = None) -> object:
        assert url.endswith("/api/status")
        payload: dict[str, object] = {
            "state": "ready" if loaded["model"] else "stopped",
            "model": loaded["model"],
            "locked": False,
            "project_model": default,
            "runtime_load_timeout": 5,
            "n_ctx": 4096 if loaded["model"] else None,
        }
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda payload=payload: payload)

    def _post(url: str, timeout: object = None) -> object:
        posts.append(url)
        loaded["model"] = url.rsplit("/api/load/", 1)[-1]
        return SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(httpx, "get", _get)
    monkeypatch.setattr(httpx, "post", _post)
    return posts


def test_probe_remote_autoloads_the_server_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # Idle unlocked serve with a default: attach loads it like the web UI does on open.
    from stabbur.cli.chat import _probe_remote

    posts = _stub_loadable_serve(monkeypatch, default="pub/Default-GGUF")
    endpoint = _probe_remote("http://127.0.0.1:8000", None, None)
    assert posts == ["http://127.0.0.1:8000/api/load/pub/Default-GGUF"]
    assert endpoint.model_name == "pub/Default-GGUF"
    assert endpoint.n_ctx == 4096  # from the ready status, not the idle one


def test_probe_remote_autoload_prefers_the_requested_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # An explicitly requested model wins over the server's default.
    from stabbur.cli.chat import _probe_remote

    posts = _stub_loadable_serve(monkeypatch, default="pub/Default-GGUF")
    endpoint = _probe_remote("http://127.0.0.1:8000", _lib_model("pub/X"), "pub/X")
    assert posts == ["http://127.0.0.1:8000/api/load/pub/X"]
    assert endpoint.model is not None and endpoint.model.name == "pub/X"  # local metadata kept


def test_probe_remote_drops_local_metadata_on_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    # The server runs a different model than the locally-resolved one: its metadata would mislead.
    from stabbur.cli.chat import _probe_remote

    _stub_httpx_get(monkeypatch, {"/api/status": {"state": "running", "model": "pub/Other-GGUF"}})
    endpoint = _probe_remote("http://127.0.0.1:8000", _lib_model("pub/X"), "pub/X")
    assert endpoint.model is None
    assert endpoint.model_name == "pub/Other-GGUF"  # the server's model wins


def _stub_generate_reply(monkeypatch: pytest.MonkeyPatch, reply: str) -> None:
    """Point a no-tools `-p` chat at a fixed reply (no runtime, no serve)."""
    from stabbur import capabilities, runtime
    from stabbur.runtime import serve_registry

    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("pub/X")])
    monkeypatch.setattr(capabilities, "capabilities", lambda _m: capabilities.ModelCapabilities())
    monkeypatch.setattr(serve_registry, "discover", lambda _name: None)
    monkeypatch.setattr(runtime, "generate", lambda *a, **k: reply)


def test_chat_p_piped_prints_raw_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not a TTY (CliRunner) -> raw text passes through untouched, safe to pipe.
    monkeypatch.setattr(cli.chat, "_isatty", lambda: False)
    _stub_generate_reply(monkeypatch, "# Title\n\n| a | b |\n| - | - |")
    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools"])
    assert result.exit_code == 0, result.output
    assert "# Title" in result.output  # literal markdown, not rendered


def test_chat_p_tty_renders_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    # A real terminal -> render markdown (like git/bat/ls colorize on a TTY).
    monkeypatch.setattr(cli.chat, "_isatty", lambda: True)
    rendered: list[str] = []
    monkeypatch.setattr(cli.chat, "_render_markdown", lambda text: rendered.append(text))
    _stub_generate_reply(monkeypatch, "# Title")
    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools"])
    assert result.exit_code == 0, result.output
    assert rendered == ["# Title"]  # routed through the renderer, not printed raw


def test_chat_p_raw_flag_forces_raw_on_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    # --raw wins even on a TTY: no rendering, literal text out.
    monkeypatch.setattr(cli.chat, "_isatty", lambda: True)
    monkeypatch.setattr(cli.chat, "_render_markdown", lambda text: pytest.fail("must not render with --raw"))
    _stub_generate_reply(monkeypatch, "# Title")
    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools", "--raw"])
    assert result.exit_code == 0, result.output
    assert "# Title" in result.output


def test_mcp_tools_lists_tools_by_server(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur import mcpservers

    servers = [
        mcpservers.McpServer(name="datetime", command="stabbur-mcp-datetime"),
        mcpservers.McpServer(name="weather-yr", command="stabbur-mcp-weather-yr"),
    ]
    monkeypatch.setattr(mcpservers, "resolve", lambda *a, **k: servers)
    grouped = {
        "datetime": [("today", "Today's date.")],
        "weather-yr": [("weather_forecast", "Weather for a named place.")],  # hyphen name resolves
    }
    monkeypatch.setattr(cli.project, "_connect_project_tools", lambda mcp: (grouped, None, []))
    result = runner.invoke(cli.app, ["mcp", "tools"])
    assert result.exit_code == 0, result.output
    assert "today" in result.output and "weather_forecast" in result.output
    assert "Weather for a named place." in result.output
    assert "3 tools across 2 server(s)" not in result.output  # 2 tools, not miscounted
    assert "2 tools across 2 server(s)" in result.output


def test_mcp_tools_none_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur import mcpservers

    monkeypatch.setattr(mcpservers, "resolve", lambda *a, **k: [])
    result = runner.invoke(cli.app, ["mcp", "tools"])
    assert result.exit_code == 0 and "No MCP servers configured" in result.output


def test_remote_model_id_matches_router_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer

    from stabbur.cli import chat as chat_cli

    # A llama-server router (or LM Studio) lists its own model ids, which need not exist in
    # the local library: the remote one-shot resolves the requested name against that listing.
    listing = {"data": [{"id": "example-remote-model"}, {"id": "other-remote-model"}]}
    monkeypatch.setattr(chat_cli, "_probe_json", lambda url: listing)
    assert chat_cli._remote_model_id("http://x", None) == "example-remote-model"  # no name -> first listed
    assert chat_cli._remote_model_id("http://x", "example-remote-model") == "example-remote-model"
    assert chat_cli._remote_model_id("http://x", "EXAMPLE-REMOTE-MODEL") == "example-remote-model"  # case-insensitive
    assert chat_cli._remote_model_id("http://x", "org/example-remote-model") == "example-remote-model"  # basename match
    with pytest.raises(typer.Exit):  # unknown name -> exit listing what IS available
        chat_cli._remote_model_id("http://x", "not-served")


def test_remote_model_id_no_models(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer

    from stabbur.cli import chat as chat_cli

    monkeypatch.setattr(chat_cli, "_probe_json", lambda url: None)  # nothing answering
    with pytest.raises(typer.Exit):
        chat_cli._remote_model_id("http://x", "anything")


def test_remote_model_id_prefers_loaded_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur.cli import chat as chat_cli

    # Free-play -p must not evict what the user has running: a router hot-swaps on request,
    # so with no name given the currently loaded model wins over the first listed one.
    listing = {
        "data": [
            {"id": "example-remote-model", "status": {"value": "unloaded"}},
            {"id": "some-remote-model", "status": {"value": "loaded"}},
        ]
    }
    monkeypatch.setattr(chat_cli, "_probe_json", lambda url: listing)
    assert chat_cli._remote_model_id("http://x", None) == "some-remote-model"


def test_probe_remote_attach_prefers_loaded_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur.cli import chat as chat_cli

    # Interactive attach to a plain OpenAI server (no /api/status): the session must start on
    # the model the server has LOADED, not the first listed one — a router hot-swaps on
    # request, so first-listed would both mislabel the session and evict the loaded model.
    def fake_probe(url: str) -> dict | None:
        if url.endswith("/api/status"):
            return None  # not a stabbur serve
        return {
            "data": [
                {"id": "example-remote-model", "status": {"value": "unloaded"}},
                {"id": "some-remote-model", "status": {"value": "loaded"}},
            ]
        }

    monkeypatch.setattr(chat_cli, "_probe_json", fake_probe)
    endpoint = chat_cli._probe_remote("http://x", None, None)
    assert endpoint.model_id == "some-remote-model"
    assert endpoint.model_name == "some-remote-model"


def test_config_set_port_pins_serve_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("STABBUR_PORT", raising=False)
    res = runner.invoke(cli.app, ["config", "set", "port", "8990"])
    assert res.exit_code == 0, res.output
    from stabbur import userconfig
    from stabbur.config import Settings

    assert userconfig.read()["port"] == 8990  # stored as a real TOML integer
    assert Settings().port == 8990  # and resolves through the settings chain (what serve binds)
    got = runner.invoke(cli.app, ["config", "get", "port"])
    assert got.exit_code == 0 and got.output.strip() == "8990"

    # Not an int / out of range -> clean error, nothing written.
    for bad in ("http://x:1234", "0", "70000"):
        res = runner.invoke(cli.app, ["config", "set", "port", bad])
        assert res.exit_code == 1, bad
    assert userconfig.read()["port"] == 8990


def test_serve_port_default_is_fixed() -> None:
    # The serve URL must be stable across restarts (bookmarks, the extension origin,
    # `stabbur chat --server`), so the port is a fixed default rather than auto-picked.
    from stabbur.config import DEFAULT_SERVE_PORT, Settings

    assert DEFAULT_SERVE_PORT == 2222
    assert Settings().port == DEFAULT_SERVE_PORT


def test_serve_refuses_a_busy_port_instead_of_moving(monkeypatch: pytest.MonkeyPatch) -> None:
    # A taken port is reported, never silently worked around — a wandering URL is worse
    # than a clear failure the user can act on.
    import socket

    monkeypatch.setattr("stabbur.cli.serve.project.load", lambda *a, **k: None)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        busy = held.getsockname()[1]
        result = runner.invoke(cli.app, ["serve", "--port", str(busy)])
    assert result.exit_code == 1, result.output
    assert "already in use" in result.output
    assert "--port" in result.output  # tells the user how to move it


def test_port_free_ignores_time_wait_leftovers() -> None:
    # Restarting right after stopping a serve must work: uvicorn closes its keep-alives on
    # shutdown, so the *server* side sits in TIME_WAIT for ~15s. The pre-flight binds the way
    # uvicorn does (SO_REUSEADDR) or it refuses a port uvicorn would have taken.
    import socket

    from stabbur.cli.serve import _port_free

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    assert not _port_free("127.0.0.1", port)  # a live listener is still a real collision

    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = srv.accept()
    conn.close()  # server closes first -> its socket enters TIME_WAIT on `port`
    client.close()
    srv.close()

    assert _port_free("127.0.0.1", port)


def test_chat_save_writes_the_exchange(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `-p --save` records the one-shot exchange in the same Markdown the TUI's /export writes,
    # so a scripted run leaves a transcript without a second tool.
    from stabbur import capabilities, runtime

    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("pub/X")])
    monkeypatch.setattr(capabilities, "capabilities", lambda _m: capabilities.ModelCapabilities())
    monkeypatch.setattr(runtime, "generate", lambda *a, **k: "the answer")
    dest = tmp_path / "chat.md"

    result = runner.invoke(
        cli.app,
        ["chat", "pub/X", "-p", "hi there", "--no-tools", "--raw", "--save", str(dest)],
    )
    assert result.exit_code == 0, result.output
    saved = dest.read_text(encoding="utf-8")
    assert "# Chat — pub/X" in saved
    assert "hi there" in saved and "the answer" in saved


def test_chat_save_failure_does_not_fail_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The answer already reached stdout, so an unwritable --save path must be reported and
    # otherwise ignored — a scripted pipeline should not die over its logging.
    from stabbur import capabilities, runtime

    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("pub/X")])
    monkeypatch.setattr(capabilities, "capabilities", lambda _m: capabilities.ModelCapabilities())
    monkeypatch.setattr(runtime, "generate", lambda *a, **k: "the answer")

    unwritable = tmp_path / "missing-dir" / "chat.md"  # parent does not exist
    result = runner.invoke(
        cli.app,
        ["chat", "pub/X", "-p", "hi", "--no-tools", "--raw", "--save", str(unwritable)],
    )
    assert result.exit_code == 0, result.output
    assert "the answer" in result.output  # the reply still came through
    assert "--save failed" in result.output


def _run_main(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    """Run `cli.main()` against an app that raises ``exc`` — what its clean-message layer sees."""
    from stabbur.cli import _app as cli_app
    from stabbur.runtime import supervisor

    def _raise() -> None:
        raise exc

    monkeypatch.setattr(supervisor, "sweep_orphans", lambda: None)  # hermetic: no runtime dir scan
    monkeypatch.setattr(cli_app, "app", _raise)
    with pytest.raises(SystemExit) as raised:
        cli_app.main()
    assert raised.value.code == 1


def test_main_surfaces_a_bad_mcp_json_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A malformed ./.mcp.json (hand-edited, like stabbur.toml) must print one line, not a Rich
    # traceback — McpConfigError's docstring promises exactly that.
    from stabbur.mcpservers import McpConfigError

    _run_main(monkeypatch, McpConfigError("/x/.mcp.json is not valid JSON: line 3"))
    out = capsys.readouterr().out
    assert "Config error" in out and "not valid JSON" in out
    assert "Traceback" not in out


def test_main_does_not_let_rich_eat_toml_table_names(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The message names the very tables the user has to fix; unescaped, Rich reads [assistant] and
    # [[assistants]] as markup and swallows them ("declares both  and []").
    from stabbur import project

    _run_main(monkeypatch, project.ProjectError("declares both [assistant] and [[assistants]]"))
    out = capsys.readouterr().out
    assert "[assistant]" in out and "[[assistants]]" in out


def test_main_does_not_let_rich_eat_a_library_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Same for the unconfigured-library message: whatever the exception says reaches the user verbatim.
    from stabbur import library as lib

    _run_main(monkeypatch, lib.LibraryNotConfigured("set STABBUR_LIBRARY_ROOT=[your drive]"))
    assert "[your drive]" in capsys.readouterr().out


def test_main_formats_manifest_warnings_as_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    # A manifest note (an absolute `libraries` entry, leftover [[mcp]]) is for the user, so it
    # prints as one sentence — not behind Python's default file/line/source-line preamble.
    import warnings

    from stabbur.cli import _app as cli_app

    monkeypatch.setattr(warnings, "formatwarning", warnings.formatwarning)  # restored after the test
    cli_app._install_one_line_warnings()

    user = warnings.formatwarning("libraries entry '/opt/models' is absolute", UserWarning, "project.py", 12)
    assert user == "Warning: libraries entry '/opt/models' is absolute\n"

    # Anything else keeps the developer-facing format, which names where it came from.
    dev = warnings.formatwarning("an internal note", DeprecationWarning, "project.py", 12)
    assert "project.py" in dev and "DeprecationWarning" in dev


def test_voice_speak_rejects_an_out_of_range_speed() -> None:
    """The engine's own ValueError reached the terminal as a traceback; catch it at the flag."""
    result = runner.invoke(cli.app, ["voice", "speak", "--speed", "9", "hello"])
    assert result.exit_code == 1
    assert "--speed must be between 0.5 and 2.0" in result.output
    assert "Traceback" not in result.output


def test_voice_speak_rejects_a_speed_the_help_used_to_advertise() -> None:
    """0.25-0.49 was documented and guaranteed to crash: the engine's floor is 0.5."""
    result = runner.invoke(cli.app, ["voice", "speak", "--speed", "0.3", "hello"])
    assert result.exit_code == 1
    assert "0.5" in result.output


def test_install_to_ollama_says_the_model_lacks_a_gguf_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """An MLX-only model is in the library; --to ollama must not claim otherwise.

    `--to ollama` pins the lookup to GGUF, so an MLX-only model resolved to nothing and the
    generic resolver reported "is not in the library" — false, and it sends the reader hunting
    for a model that is sitting right there.
    """
    mlx = _lib_model("pub/Only-MLX", fmt=ModelFormat.mlx)
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [mlx] if k.get("model_format") is None else [])
    result = runner.invoke(cli.app, ["library", "install", "pub/Only-MLX", "--to", "ollama"])
    assert result.exit_code == 1
    assert "not in the library" not in result.output
    assert "no GGUF build" in result.output
    assert "mlx" in result.output and "--to lmstudio" in result.output


def test_doctor_table_does_not_let_rich_eat_an_install_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The MLX hint's own command contains `[mlx]`, which Rich reads as a style tag and swallows —
    # doctor and setup printed `uv tool install --force -e "."`, a command that installs stabbur
    # without the MLX extra, in the very row that exists to fix a missing MLX runtime.
    from stabbur import doctor as doctor_mod
    from stabbur import host
    from stabbur.cli import health

    monkeypatch.setattr(health.console, "width", 300)  # one line per row, so nothing wraps mid-hint
    report = doctor_mod.DoctorReport(
        checks=[
            doctor_mod.Check(
                name="MLX text (mlx-lm)",
                status=doctor_mod.CheckStatus.warn,
                detail="'mlx_lm.server' not found",
                hint=host.install_hints()["mlx_lm.server"],
            )
        ]
    )
    health._print_doctor_table(report)
    out = capsys.readouterr().out
    assert '".[mlx]"' in out  # the extra survives, so the printed command actually installs it


# --- `stabbur chat` with tools: which servers get spawned, and what a failure says --------------------


class _StubToolset:
    """Enough of an ``MCPToolset`` for a one-shot with a stubbed ``agent.run``."""

    schemas: list[dict[str, object]] = []

    def __init__(self, errors: list[tuple[str, str]] | None = None) -> None:
        self.errors = errors or []

    @property
    def names(self) -> list[str]:
        return []


def _stub_chat_with_tools(
    monkeypatch: pytest.MonkeyPatch, resolved: list[object], *, errors: list[tuple[str, str]] | None = None
) -> dict[str, object]:
    """Run ``chat -p`` against stubs and capture the server specs ``tools.connect`` was handed."""
    import contextlib
    from collections.abc import AsyncGenerator
    from typing import Any

    from stabbur import agent, capabilities, mcpservers, runtime, tools
    from stabbur.runtime import serve_registry

    captured: dict[str, object] = {}
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("pub/X")])
    monkeypatch.setattr(capabilities, "capabilities", lambda _m: capabilities.ModelCapabilities())
    monkeypatch.setattr(serve_registry, "discover", lambda _name: None)
    monkeypatch.setattr(mcpservers, "resolve", lambda *a, **k: list(resolved))
    monkeypatch.setattr(runtime, "load", lambda _m: type("R", (), {"base": "http://runtime"})())
    monkeypatch.setattr(runtime, "stop", lambda _rt: None)

    @contextlib.asynccontextmanager
    async def _connect(servers: Any) -> AsyncGenerator[_StubToolset, None]:
        captured["servers"] = list(servers)
        yield _StubToolset(errors)

    async def _run(base: str, messages: Any, toolset: Any, max_tokens: Any, on_event: Any, on_token: Any, **kw: Any):
        on_token("ok")  # the CLI's sink is sync (it prints); the loop calls it directly
        return "ok"

    monkeypatch.setattr(tools, "connect", _connect)
    monkeypatch.setattr(agent, "run", _run)
    return captured


def test_chat_mcp_duplicate_of_a_configured_server_is_not_spawned_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    # `--mcp datetime` where .mcp.json already configures the identical server: one process, one
    # namespace. Two copies gave the model the same tools under `datetime__*` and `datetime2__*`.
    from stabbur.mcpservers import McpServer

    configured = McpServer(name="datetime", command="stabbur-mcp-datetime")
    captured = _stub_chat_with_tools(monkeypatch, [configured])
    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--mcp", "datetime"])
    assert result.exit_code == 0, result.output
    assert captured["servers"] == [configured.to_spec()]


def test_chat_mcp_extras_never_take_a_configured_server_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    # A --mcp that is genuinely different but slugs to the same prefix is appended, so the CONFIGURED
    # server keeps the bare prefix that build_target_routing predicted for it. Ordered the other way
    # round, the extra took `datetime` and --target scoped the target to the wrong server.
    from stabbur import tools
    from stabbur.mcpservers import McpServer

    configured = McpServer(name="datetime", command="stabbur-mcp-datetime")
    captured = _stub_chat_with_tools(monkeypatch, [configured])
    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--mcp", "stabbur-mcp-datetime --utc"])
    assert result.exit_code == 0, result.output
    specs = captured["servers"]
    assert isinstance(specs, list) and len(specs) == 2
    assert specs[0] == configured.to_spec()  # configured first, extras after
    prefixes = tools.assign_prefixes(specs)
    assert prefixes == ["datetime", "datetime2"]
    assert prefixes[0] == tools._prefix_by_name([configured])["datetime"]  # what --target routes on


def test_chat_warns_about_an_mcp_command_that_does_not_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    # A typo'd --mcp used to produce a session with no tools, no message, and exit 0.
    captured = _stub_chat_with_tools(monkeypatch, [])
    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--mcp", "stabbur-not-a-real-server"])
    assert result.exit_code == 0, result.output
    assert "no such command" in result.output and "stabbur-not-a-real-server" in result.output
    assert captured["servers"]  # still attempted, so connect() records the real failure too


def test_chat_reports_a_server_that_failed_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    # connect() records per-server failures instead of raising; the one-shot has to say so (on stderr,
    # so stdout stays exactly the answer) rather than answer as if the tools were never asked for.
    _stub_chat_with_tools(monkeypatch, [], errors=[("bogus", "[Errno 2] no such file")])
    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--mcp", "stabbur-mcp-datetime"])
    assert result.exit_code == 0, result.output
    assert "did not start" in result.output and "bogus" in result.output


# --- `stabbur chat --image`: attachments are checked before anything is sent --------------------------


def _stub_oneshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enough stubs for `chat -p --no-tools` to reach (and print) an answer."""
    from stabbur import capabilities, runtime
    from stabbur.runtime import serve_registry

    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_lib_model("pub/X")])
    monkeypatch.setattr(capabilities, "capabilities", lambda _m: capabilities.ModelCapabilities(vision=True))
    monkeypatch.setattr(serve_registry, "discover", lambda _name: None)
    monkeypatch.setattr(runtime, "generate", lambda *a, **k: "ok")


def test_chat_image_must_exist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_oneshot(monkeypatch)
    missing = tmp_path / "shot.png"
    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools", "-i", str(missing)])
    assert result.exit_code == 1
    assert "image file not found" in result.output  # not "Vision not found", which is not a thing


def test_chat_image_must_actually_be_an_image(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Unchecked, a non-image was base64'd into the request and the only symptom was the runtime's own
    # HTTP error, naming an ephemeral internal port and saying nothing about the attachment.
    _stub_oneshot(monkeypatch)
    notes = tmp_path / "notes.png"
    notes.write_text("this is not a png")
    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools", "-i", str(notes)])
    assert result.exit_code == 1
    assert "not an image file" in result.output


def test_chat_accepts_a_real_image(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_oneshot(monkeypatch)
    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    result = runner.invoke(cli.app, ["chat", "pub/X", "-p", "hi", "--no-tools", "-i", str(png)])
    assert result.exit_code == 0, result.output


def test_clean_error_hides_the_internal_runtime_url() -> None:
    # The runtime stabbur spawned answers on an ephemeral loopback port the user never chose; putting
    # it in the error explains nothing ("413 Payload Too Large for url http://127.0.0.1:<port>/...").
    import httpx

    url = "http://127.0.0.1:51234/v1/chat/completions"
    status = httpx.HTTPStatusError(
        f"Client error '413 Payload Too Large' for url '{url}'",
        request=httpx.Request("POST", url),
        response=httpx.Response(413, request=httpx.Request("POST", url)),
    )
    message = cli.chat._clean_error(status)
    assert "127.0.0.1" not in message and "too large" in message

    # A non-HTTP failure keeps its own words, minus any loopback URL...
    assert cli.chat._clean_error(RuntimeError(f"runtime exited, see {url}")) == "runtime exited, see"
    # ...while a host the user typed is the whole point of the message and stays.
    assert "gpu-box" in cli.chat._clean_error(RuntimeError("cannot reach http://gpu-box:1234/v1"))
