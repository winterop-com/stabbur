"""Tests for the want list — `heim library manifest` (export) and `heim library sync` (re-pull)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heim import cli, wantlist
from heim import library as library_ops
from heim.models import ModelSource, PullResult

runner = CliRunner()


def _hf_model(root: Path, repo: str, *, fmt: str = "gguf", source: str = "huggingface") -> None:
    """Create a directory model under ``<root>/<fmt>/<repo>`` with a real ``.heim`` sidecar."""
    weight = "model.Q4_K_M.gguf" if fmt == "gguf" else "model.safetensors"
    model_dir = root / fmt / repo
    model_dir.mkdir(parents=True)
    (model_dir / weight).write_bytes(b"w" * 100)
    if fmt != "gguf":
        (model_dir / "config.json").write_text(json.dumps({"architectures": ["LlamaForCausalLM"]}))
    sidecar = model_dir / ".heim"
    sidecar.mkdir()
    meta: dict[str, object] = {"source": source, "name": repo, "size_bytes": 100, "file_count": 1}
    if source == "lmstudio":
        meta["format"] = fmt
    (sidecar / "metadata.json").write_text(json.dumps(meta))


def _ollama_model(root: Path, name: str) -> None:
    """Create a minimal restorable Ollama model (manifest + one blob) under ``<root>/ollama``."""
    blob = "sha256-" + "a" * 64
    blobs = root / "ollama" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / blob).write_bytes(b"gguf-weights")
    repo, tag = name.split(":")
    manifest = root / "ollama" / "manifests" / "registry.ollama.ai" / "library" / repo / tag
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": f"sha256:{'a' * 64}"}]})
    )


# --- export (manifest) --------------------------------------------------------------------------


def test_collect_hf_and_ollama_entries(tmp_path: Path) -> None:
    _hf_model(tmp_path, "unsloth/Llama-3.2-1B-Instruct-GGUF")
    _ollama_model(tmp_path, "gemma3:latest")
    entries, comments = wantlist.collect(library_ops.scan(root=tmp_path))

    assert comments == []
    by_source = {e.source: e for e in entries}
    assert by_source["huggingface"].name == "unsloth/Llama-3.2-1B-Instruct-GGUF"
    assert by_source["huggingface"].model_format == "gguf"
    assert by_source["ollama"].name == "gemma3:latest"
    assert by_source["ollama"].model_format == "gguf"


def test_lmstudio_backup_exported_as_hf_equivalent(tmp_path: Path) -> None:
    _hf_model(tmp_path, "lmstudio-community/Qwen2.5-7B-Instruct-GGUF", source="lmstudio")
    entries, _ = wantlist.collect(library_ops.scan(root=tmp_path))
    assert len(entries) == 1
    entry = entries[0]
    assert entry.source == "huggingface"  # not re-downloadable as LM Studio → its HF repo
    assert entry.name == "lmstudio-community/Qwen2.5-7B-Instruct-GGUF"
    assert "LM Studio" in entry.note
    # the note renders as a comment, not a TOML key
    text = wantlist.render(entries)
    assert "# from an LM Studio backup" in text
    assert "note" not in text


def test_render_parse_round_trip(tmp_path: Path) -> None:
    _hf_model(tmp_path, "unsloth/Llama-3.2-1B-Instruct-GGUF")
    _ollama_model(tmp_path, "gemma3:latest")
    entries, comments = wantlist.collect(library_ops.scan(root=tmp_path))
    reparsed = wantlist.parse(wantlist.render(entries, comments))
    assert {(e.source, e.name, e.model_format) for e in reparsed} == {
        (e.source, e.name, e.model_format) for e in entries
    }


def test_parse_rejects_incomplete_entry() -> None:
    with pytest.raises(ValueError, match="source"):
        wantlist.parse('[[model]]\nname = "foo"\n')


# --- diff / sync --------------------------------------------------------------------------------


def test_plan_partitions_present_and_missing(tmp_path: Path) -> None:
    _hf_model(tmp_path, "pub/Present-GGUF")
    scanned = library_ops.scan(root=tmp_path)
    wants = [
        wantlist.WantModel(source="huggingface", name="pub/Present-GGUF", model_format="gguf"),
        wantlist.WantModel(source="huggingface", name="pub/Missing-GGUF", model_format="gguf"),
    ]
    sp = wantlist.plan(wants, scanned)
    assert [w.name for w in sp.present] == ["pub/Present-GGUF"]
    assert [w.name for w in sp.missing] == ["pub/Missing-GGUF"]


def test_round_trip_manifest_then_sync_is_a_noop(tmp_path: Path) -> None:
    _hf_model(tmp_path, "unsloth/Llama-3.2-1B-Instruct-GGUF")
    _ollama_model(tmp_path, "gemma3:latest")
    scanned = library_ops.scan(root=tmp_path)
    entries, _ = wantlist.collect(scanned)
    sp = wantlist.plan(entries, scanned)
    assert sp.missing == []
    assert len(sp.present) == len(entries)


def test_sync_dry_run_lists_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _hf_model(tmp_path, "pub/Have-GGUF")
    monkeypatch.setattr(library_ops, "scan", lambda: library_ops._scan.scan(root=tmp_path))
    monkeypatch.setattr(library_ops, "roots", lambda *a, **k: [tmp_path])
    want = tmp_path / "want.toml"
    want.write_text(
        wantlist.render(
            [
                wantlist.WantModel(source="huggingface", name="pub/Have-GGUF", model_format="gguf"),
                wantlist.WantModel(source="huggingface", name="pub/Need-GGUF", model_format="gguf"),
            ]
        )
    )

    def _boom(*a: object, **k: object) -> PullResult:
        raise AssertionError("dry-run must not pull")

    monkeypatch.setattr(wantlist, "pull_entry", _boom)
    result = runner.invoke(cli.app, ["library", "sync", str(want), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "have" in result.output and "pub/Have-GGUF" in result.output
    assert "pub/Need-GGUF" in result.output
    assert "1 to pull" in result.output


def test_sync_pulls_missing_and_continues_past_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _hf_model(tmp_path, "pub/Have-GGUF")
    monkeypatch.setattr(library_ops, "scan", lambda: library_ops._scan.scan(root=tmp_path))
    monkeypatch.setattr(library_ops, "roots", lambda *a, **k: [tmp_path])
    want = tmp_path / "want.toml"
    want.write_text(
        wantlist.render(
            [
                wantlist.WantModel(source="huggingface", name="pub/Have-GGUF", model_format="gguf"),
                wantlist.WantModel(source="huggingface", name="pub/Good-GGUF", model_format="gguf"),
                wantlist.WantModel(source="huggingface", name="pub/Bad-GGUF", model_format="gguf"),
            ]
        )
    )
    pulled: list[str] = []

    def _fake_pull(entry: wantlist.WantModel, root: Path | None) -> PullResult:
        if entry.name == "pub/Bad-GGUF":
            raise RuntimeError("repo not found")
        pulled.append(entry.name)
        return PullResult(source=ModelSource.huggingface, name=entry.name, destination=tmp_path, size_bytes=1)

    monkeypatch.setattr(wantlist, "pull_entry", _fake_pull)
    result = runner.invoke(cli.app, ["library", "sync", str(want)])
    assert result.exit_code == 1, result.output  # a failure → non-zero exit
    assert pulled == ["pub/Good-GGUF"]  # the good one still pulled despite the bad one failing
    assert "1 pulled" in result.output
    assert "1 failed" in result.output
    assert "pub/Bad-GGUF" in result.output


def test_manifest_save_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _hf_model(tmp_path, "pub/Chat-GGUF")
    monkeypatch.setattr(library_ops, "scan", lambda: library_ops._scan.scan(root=tmp_path))
    monkeypatch.setattr(library_ops, "roots", lambda *a, **k: [tmp_path])
    out = tmp_path / "models.toml"
    result = runner.invoke(cli.app, ["library", "manifest", "--save", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file()
    parsed = wantlist.parse(out.read_text())
    assert [(e.source, e.name) for e in parsed] == [("huggingface", "pub/Chat-GGUF")]


def test_ollama_source_bypasses_sidecar(tmp_path: Path) -> None:
    # Regression: an Ollama model has no .heim sidecar on the model dir; it must still classify.
    _ollama_model(tmp_path, "qwen3:4b")
    (scanned,) = [m for m in library_ops.scan(root=tmp_path)]
    entry = wantlist.entry_for(scanned)
    assert isinstance(entry, wantlist.WantModel)
    assert (entry.source, entry.name) == ("ollama", "qwen3:4b")
