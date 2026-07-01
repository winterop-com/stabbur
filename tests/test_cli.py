"""CLI behavior tests (Typer CliRunner) — library-centric list + chat-only guard."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from kodo import catalog as catalog_ops
from kodo import cli
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


def test_list_shows_the_library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library_ops, "scan", lambda: [_lib_model("pub/Chat-GGUF")])
    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "pub/Chat-GGUF" in result.output
    assert "in your library" in result.output


def test_ls_is_an_alias_for_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library_ops, "scan", lambda: [_lib_model("pub/Chat-GGUF")])
    assert runner.invoke(cli.app, ["ls"]).exit_code == 0


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
        first = runner.invoke(cli.app, ["init", "--model", "unsloth/X-GGUF"])
        assert first.exit_code == 0, first.output
        manifest = Path("kodo.toml")
        assert manifest.exists()
        text = manifest.read_text()
        assert 'model = "unsloth/X-GGUF"' in text
        assert "library_root =" in text  # library config lives in kodo.toml, not .env

        again = runner.invoke(cli.app, ["init", "--model", "unsloth/X-GGUF"])
        assert again.exit_code == 1  # refuses to clobber an existing project
        assert "already exists" in again.output


def _pull_result(name: str) -> PullResult:
    return PullResult(
        source=ModelSource.lmstudio, name=name, destination=Path("/tmp") / name, size_bytes=10, file_count=1
    )


def test_pull_requires_name_or_all() -> None:
    # Neither name nor --all → usage error; both → usage error.
    neither = runner.invoke(cli.app, ["pull", "ollama"])
    assert neither.exit_code == 2
    assert "either a model name or --all" in neither.output
    both = runner.invoke(cli.app, ["pull", "ollama", "some:model", "--all"])
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
    result = runner.invoke(cli.app, ["pull", "lmstudio", "--all"])
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
    result = runner.invoke(cli.app, ["pull", "lmstudio", "--all"])
    assert result.exit_code == 1  # some failed
    assert "1 imported" in result.output
    assert "1 failed" in result.output
    assert "blob missing" in result.output


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
    default = runner.invoke(cli.app, ["sources"])
    assert "pub/Chat-GGUF" in default.output
    assert "all-MiniLM" not in default.output  # embedding hidden by default
    assert "hidden" in default.output

    everything = runner.invoke(cli.app, ["sources", "--all"])
    assert "all-MiniLM" in everything.output  # shown with --all
