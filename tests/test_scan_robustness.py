"""Regression tests for scan/removal robustness (REVIEW.md batch 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kodo import arch, library, tags
from kodo.models import ModelFormat
from kodo.sources import base


def _gguf(dirpath: Path, name: str = "model.gguf", data: bytes = b"weights") -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / name).write_bytes(data)


# --- N-H1: a non-dict config.json must not crash classification ---


def test_config_is_generative_handles_non_dict_json(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text("[]")  # valid JSON, not an object (corruption / truncation-repair)
    assert arch.config_is_generative(cfg) is None
    cfg.write_text('"a bare string"')
    assert arch.config_is_generative(cfg) is None
    cfg.write_text('{"architectures": ["LlamaForCausalLM"]}')
    assert arch.config_is_generative(cfg) is True


# --- S-N1 + S-M7: scan skips nameless loose weights and dot-dir staging ---


def test_scan_skips_loose_weight_and_staging(tmp_path: Path) -> None:
    _gguf(tmp_path / "gguf" / "pub" / "Real-GGUF")  # a real model
    _gguf(tmp_path / "gguf")  # a loose weight at the bucket root -> nameless, must be skipped
    _gguf(tmp_path / "gguf" / ".kodo-stage-abc" / "Repo")  # interrupted-pull staging, must be skipped
    names = {m.name for m in library.scan(root=tmp_path)}
    assert names == {"pub/Real-GGUF"}


# --- base.copy_verified: per-file (not aggregate-total) verification ---


def test_copy_verified_matches_and_detects_drift(tmp_path: Path) -> None:
    src, dest = tmp_path / "src", tmp_path / "dest"
    _gguf(src, "a.gguf", b"aaaa")
    (src / "b.txt").write_text("bb")
    _gguf(dest, "a.gguf", b"aaaa")
    (dest / "b.txt").write_text("bb")
    assert base.copy_verified(src, dest) is True

    (dest / "b.txt").write_text("DIFFERENT-LENGTH")  # same file set, one size differs
    assert base.copy_verified(src, dest) is False

    (dest / "b.txt").write_text("bb")  # restore, then drop a file
    (dest / "a.gguf").unlink()
    assert base.copy_verified(src, dest) is False


# --- C-N2: remove reports nothing freed when the files can't be deleted ---


def test_remove_reports_failure_when_dir_survives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "gguf" / "pub" / "Foo-GGUF"
    _gguf(repo, "m.gguf", b"weights")
    model = library.LibraryModel(
        name="pub/Foo-GGUF",
        model_format=ModelFormat.gguf,
        path=repo,
        load_target=repo / "m.gguf",
        size_bytes=7,
        file_count=1,
    )
    # Simulate rmtree failing silently (ignore_errors=True) — the dir remains.
    monkeypatch.setattr(library.shutil, "rmtree", lambda *a, **k: None)
    assert library.remove(model) == (0, 0)  # honest: nothing removed


def test_remove_drops_model_tags(tmp_path: Path) -> None:
    # C-12: a removed model must not leave stale tags a re-pull would silently inherit.
    repo = tmp_path / "gguf" / "pub" / "Foo-GGUF"
    _gguf(repo, "m.gguf", b"weights")
    tags.set_tags(tmp_path, "pub/Foo-GGUF", ["tested", "fast"])
    model = library.LibraryModel(
        name="pub/Foo-GGUF",
        model_format=ModelFormat.gguf,
        path=repo,
        load_target=repo / "m.gguf",
        library_root=tmp_path,
        size_bytes=7,
        file_count=1,
    )
    library.remove(model)
    assert not repo.exists()
    assert tags.tags_for(tmp_path, "pub/Foo-GGUF") == []  # tags cleaned


def test_migrate_keeps_hf_copy_when_bucket_copy_is_not_a_verified_dup(tmp_path: Path) -> None:
    # S-M2: a partial/different dir already in the bucket must NOT license deleting the complete
    # huggingface/ copy. No dedup is planned, and apply leaves the hf copy intact.
    hf = tmp_path / "huggingface" / "pub" / "Foo-GGUF"
    _gguf(hf, "m.gguf", b"complete-weights")
    _gguf(tmp_path / "gguf" / "pub" / "Foo-GGUF", "m.gguf", b"x")  # different size -> not verified
    plan = library.plan_migration(tmp_path)
    assert all(a.kind != "dedup" for a in plan)
    library.apply_migration(plan)
    assert (hf / "m.gguf").read_bytes() == b"complete-weights"  # hf copy untouched


def test_migrate_move_does_not_nest_into_a_racing_dest(tmp_path: Path) -> None:
    # S-M2: if a dest appears between plan (move) and apply, don't move src *into* it (nesting).
    hf = tmp_path / "huggingface" / "pub" / "Bar-GGUF"
    _gguf(hf, "m.gguf", b"data")
    plan = library.plan_migration(tmp_path)
    assert plan and plan[0].kind == "move"
    _gguf(tmp_path / "gguf" / "pub" / "Bar-GGUF", "other.gguf", b"different")  # racing pull, different content
    library.apply_migration(plan)
    assert not (tmp_path / "gguf" / "pub" / "Bar-GGUF" / "Bar-GGUF").exists()  # no nesting
    assert (hf / "m.gguf").exists()  # conflict → hf copy left untouched
