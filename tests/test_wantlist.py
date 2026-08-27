"""Tests for the want list — `stabbur library manifest` (export) and `stabbur library sync` (re-pull)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stabbur import cli, wantlist
from stabbur import library as library_ops
from stabbur.models import ModelSource, PullResult

runner = CliRunner()


def _hf_model(root: Path, repo: str, *, fmt: str = "gguf", source: str = "huggingface") -> None:
    """Create a directory model under ``<root>/<fmt>/<repo>`` with a real ``.stabbur`` sidecar."""
    weight = "model.Q4_K_M.gguf" if fmt == "gguf" else "model.safetensors"
    model_dir = root / fmt / repo
    model_dir.mkdir(parents=True)
    (model_dir / weight).write_bytes(b"w" * 100)
    if fmt != "gguf":
        (model_dir / "config.json").write_text(json.dumps({"architectures": ["LlamaForCausalLM"]}))
    sidecar = model_dir / ".stabbur"
    sidecar.mkdir()
    meta: dict[str, object] = {"source": source, "name": repo, "size_bytes": 100, "file_count": 1}
    if source == "lmstudio":
        meta["format"] = fmt
    (sidecar / "metadata.json").write_text(json.dumps(meta))


def _quant_subdir_model(root: Path, repo: str, quant: str, *, include: list[str] | None = None) -> None:
    """A repo whose quant lives in its own folder — the sidecar sits on the *repo* dir, one level up.

    This is the layout multi-quant GGUF publishers use, and the one that produced want-list names
    like ``pub/Repo-GGUF/UD-Q3_K_XL``: three segments, so no such Hugging Face repo exists.
    """
    repo_dir = root / "gguf" / repo
    (repo_dir / quant).mkdir(parents=True)
    (repo_dir / quant / f"model-{quant}.gguf").write_bytes(b"w" * 100)
    (repo_dir / "README.md").write_text("# card")
    sidecar = repo_dir / ".stabbur"
    sidecar.mkdir()
    meta: dict[str, object] = {"source": "huggingface", "name": repo, "size_bytes": 100, "file_count": 1}
    if include is not None:
        meta["include"] = include
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


def test_parse_rejects_a_bad_include() -> None:
    with pytest.raises(ValueError, match="include"):
        wantlist.parse('[[model]]\nsource = "huggingface"\nname = "pub/Foo"\ninclude = "*Q4*"\n')


# --- partial pulls: the include globs that make a rebuild faithful -------------------------------


def test_include_recorded_at_pull_time_is_exported(tmp_path: Path) -> None:
    # Without this the want list only names the repo, so a rebuild re-downloads every quant of a
    # multi-quant repo to recreate the one copy on the drive.
    _hf_model(tmp_path, "pub/Multi-GGUF")
    sidecar = tmp_path / "gguf" / "pub" / "Multi-GGUF" / ".stabbur" / "metadata.json"
    sidecar.write_text(json.dumps(json.loads(sidecar.read_text()) | {"include": ["*Q4_K_M*"]}))

    (entry,) = wantlist.collect(library_ops.scan(root=tmp_path))[0]
    assert entry.name == "pub/Multi-GGUF"
    assert entry.include == ["*Q4_K_M*"]
    assert 'include = ["*Q4_K_M*"]' in wantlist.render([entry])
    assert wantlist.parse(wantlist.render([entry]))[0].include == ["*Q4_K_M*"]


def test_a_quant_subdirectory_exports_a_real_repo_id_plus_a_glob(tmp_path: Path) -> None:
    # The library name has three segments (pub/Repo/quant); emitting that as the want-list name
    # produced an entry no source could pull, because there is no such repo.
    _quant_subdir_model(tmp_path, "pub/Repo-GGUF", "UD-Q3_K_XL")
    (entry,) = wantlist.collect(library_ops.scan(root=tmp_path))[0]
    assert entry.name == "pub/Repo-GGUF"  # a valid two-segment repo id
    assert entry.include == ["UD-Q3_K_XL/*"]


def test_a_quant_subdirectory_prefers_the_recorded_include(tmp_path: Path) -> None:
    # What the pull asked for beats what the layout implies.
    _quant_subdir_model(tmp_path, "pub/Repo-GGUF", "UD-Q3_K_XL", include=["*UD-Q3_K_XL*"])
    (entry,) = wantlist.collect(library_ops.scan(root=tmp_path))[0]
    assert entry.name == "pub/Repo-GGUF"
    assert entry.include == ["*UD-Q3_K_XL*"]


def test_two_quants_of_one_repo_stay_distinct_entries(tmp_path: Path) -> None:
    _quant_subdir_model(tmp_path, "pub/Repo-GGUF", "UD-Q3_K_XL")
    quant = tmp_path / "gguf" / "pub" / "Repo-GGUF" / "UD-Q8_0"
    quant.mkdir(parents=True)
    (quant / "model-UD-Q8_0.gguf").write_bytes(b"w" * 100)

    entries, _ = wantlist.collect(library_ops.scan(root=tmp_path))
    assert [e.name for e in entries] == ["pub/Repo-GGUF", "pub/Repo-GGUF"]
    assert sorted(tuple(e.include) for e in entries) == [("UD-Q3_K_XL/*",), ("UD-Q8_0/*",)]


def test_a_sidecar_without_include_still_means_the_whole_repo(tmp_path: Path) -> None:
    # Backward compatibility: pulls that predate the recording keep today's behaviour.
    _hf_model(tmp_path, "pub/Whole-GGUF")
    (entry,) = wantlist.collect(library_ops.scan(root=tmp_path))[0]
    assert entry.include == []
    assert "include" not in wantlist.render([entry])


def test_pull_entry_passes_include_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from stabbur import catalog

    captured: dict[str, object] = {}

    def fake_pull(source: ModelSource, name: str, **kwargs: object) -> PullResult:
        captured.update({"source": source, "name": name, **kwargs})
        return PullResult(source=source, name=name, destination=tmp_path)

    monkeypatch.setattr(catalog, "pull", fake_pull)
    wantlist.pull_entry(
        wantlist.WantModel(source="huggingface", name="pub/Repo-GGUF", include=["UD-Q3_K_XL/*"]), tmp_path
    )
    assert captured["include"] == ["UD-Q3_K_XL/*"]

    # Ollama has no include; passing one would raise in catalog.pull.
    wantlist.pull_entry(wantlist.WantModel(source="ollama", name="gemma3:latest"), tmp_path)
    assert captured["include"] is None


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
    # Regression: an Ollama model has no .stabbur sidecar on the model dir; it must still classify.
    _ollama_model(tmp_path, "qwen3:4b")
    (scanned,) = [m for m in library_ops.scan(root=tmp_path)]
    entry = wantlist.entry_for(scanned)
    assert isinstance(entry, wantlist.WantModel)
    assert (entry.source, entry.name) == ("ollama", "qwen3:4b")


def test_plan_treats_a_damaged_model_as_missing(tmp_path: Path) -> None:
    # The repair pass: a model that is on disk but fails verification must land in `missing`,
    # so sync re-pulls over the damaged copy instead of reporting "have" and moving on.
    _hf_model(tmp_path, "pub/Broken-GGUF")
    _hf_model(tmp_path, "pub/Fine-GGUF")
    scanned = library_ops.scan(root=tmp_path)
    wants = [
        wantlist.WantModel(source="huggingface", name="pub/Broken-GGUF", model_format="gguf"),
        wantlist.WantModel(source="huggingface", name="pub/Fine-GGUF", model_format="gguf"),
    ]

    sp = wantlist.plan(wants, scanned, unhealthy=lambda m: m.name == "pub/Broken-GGUF")
    assert [w.name for w in sp.missing] == ["pub/Broken-GGUF"]
    assert [w.name for w in sp.present] == ["pub/Fine-GGUF"]

    # Without the predicate the damaged copy is indistinguishable from a good one.
    assert wantlist.plan(wants, scanned).missing == []


def test_plan_keeps_an_identity_present_when_one_copy_is_healthy(tmp_path: Path) -> None:
    # One good copy is enough: a want must not be re-pulled just because some *other* model
    # sharing its identity failed. (Guards the loop that builds the `have` set.)
    _hf_model(tmp_path, "pub/Dup-GGUF")
    scanned = list(library_ops.scan(root=tmp_path))
    wants = [wantlist.WantModel(source="huggingface", name="pub/Dup-GGUF", model_format="gguf")]

    doubled = [*scanned, *scanned]  # the same identity twice, one flagged damaged
    seen: list[str] = []

    def unhealthy(model: object) -> bool:
        seen.append("x")
        return len(seen) == 1  # only the first copy is damaged

    assert wantlist.plan(wants, doubled, unhealthy=unhealthy).missing == []
