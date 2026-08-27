"""Regression tests for scan/removal robustness (S-H2/S-N1/N-H1 hardening batch)."""

from __future__ import annotations

from pathlib import Path

import pytest

from stabbur import arch, library, tags
from stabbur.models import ModelFormat
from stabbur.sources import base


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
    _gguf(tmp_path / "gguf" / ".stabbur-stage-abc" / "Repo")  # interrupted-pull staging, must be skipped
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


def test_copy_verified_compares_bytes_not_just_sizes(tmp_path: Path) -> None:
    # A same-size, different-bytes copy (a bad write on the no-journaling drive) is not a copy:
    # sizes alone said "verified" and licensed deleting the good source.
    src, dest = tmp_path / "src", tmp_path / "dest"
    _gguf(src, "m.gguf", b"good-weights")
    _gguf(dest, "m.gguf", b"BAD!-weights")  # identical length
    assert base.copy_verified(src, dest) is False
    _gguf(dest, "m.gguf", b"good-weights")
    assert base.copy_verified(src, dest) is True


def test_copy_verified_refuses_vacuous_matches(tmp_path: Path) -> None:
    # copy_verified is the permit to delete the *only other* copy, so nothing-vs-nothing must be
    # False. Every case here used to answer True and hand a caller that permit.
    empty_src, empty_dest = tmp_path / "empty-src", tmp_path / "empty-dest"
    empty_src.mkdir()
    empty_dest.mkdir()
    assert base.copy_verified(empty_src, empty_dest) is False  # empty vs empty

    hollow_src, hollow_dest = tmp_path / "hollow-src", tmp_path / "hollow-dest"
    _gguf(hollow_src, "m.gguf", b"")  # files, but no content at all
    _gguf(hollow_dest, "m.gguf", b"")
    assert base.copy_verified(hollow_src, hollow_dest) is False

    missing = tmp_path / "not-there"
    assert base.copy_verified(hollow_src, missing) is False  # a dest that isn't there
    real = tmp_path / "real"
    _gguf(real, "m.gguf", b"weights")
    assert base.copy_verified(real, real) is False  # a dir is not a backup of itself


def test_copy_verified_follows_symlinks_like_dir_stats(tmp_path: Path) -> None:
    # The measuring side (dir_stats) followed symlinks while the verifying side skipped them, so a
    # symlinked source (the HF cache's snapshots/ layout) measured as "no files" and an EMPTY dest
    # verified clean — after which --move / migrate deleted the only real copy.
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"weights" * 512)
    src, dest = tmp_path / "src", tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    (src / "model.gguf").symlink_to(blob)

    assert base.dir_stats(src) == (blob.stat().st_size, 1)  # measured: one file with content
    assert base.copy_verified(src, dest) is False  # ... so an empty dest is not a copy of it

    (dest / "model.gguf").write_bytes(blob.read_bytes())  # what copy_tree(symlinks=False) writes
    assert base.copy_verified(src, dest) is True
    assert base.dir_stats(src) == base.dir_stats(dest)  # measure and verify agree


def test_stats_skip_dirs_match_relative_to_the_measured_root(tmp_path: Path) -> None:
    # S-M2: the skip-dirs were matched against the ABSOLUTE path's parts, so a library root that
    # itself sits under a `.stabbur/` or `.cache/` ancestor (a project's
    # ``libraries = [".stabbur/library"]``) measured every model as zero files — which made
    # copy_verified vacuously true for it.
    src = tmp_path / ".stabbur" / "library" / "gguf" / "pub" / "Foo-GGUF"
    _gguf(src, "m.gguf", b"weights")
    assert base.dir_stats(src) == (7, 1)

    dest = tmp_path / ".cache" / "other-lib" / "gguf" / "pub" / "Foo-GGUF"
    _gguf(dest, "m.gguf", b"DIFFERE")  # same size, different bytes
    assert base.copy_verified(src, dest) is False
    _gguf(dest, "m.gguf", b"weights")
    assert base.copy_verified(src, dest) is True

    # Still relative, so a model's own sidecar/bookkeeping is skipped as before.
    _gguf(src / ".stabbur", "metadata.json", b"{}")
    _gguf(src / ".cache", "download.lock", b"lock")
    assert base.dir_stats(src) == (7, 1)
    assert base.copy_verified(src, dest) is True


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
    monkeypatch.setattr(library._manage.shutil, "rmtree", lambda *a, **k: None)
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


def _symlinked_hf_model(tmp_path: Path, content: bytes = b"complete-weights") -> Path:
    """An old-layout ``huggingface/`` model whose weight file is a symlink into a blob."""
    blob = tmp_path / "blob.gguf"
    blob.write_bytes(content)
    hf = tmp_path / "huggingface" / "pub" / "Foo-GGUF"
    hf.mkdir(parents=True)
    (hf / "m.gguf").symlink_to(blob)
    return hf


def test_migrate_move_keeps_a_symlinked_source_when_the_racing_dest_is_empty(tmp_path: Path) -> None:
    # The data-loss path: the verifier skipped symlinks while dir_stats followed them, so a
    # symlinked source measured as "no files" and matched an existing-but-EMPTY bucket dir (an
    # interrupted pull). apply_migration then deleted the source — the only copy — as a dup.
    hf = _symlinked_hf_model(tmp_path)
    plan = library.plan_migration(tmp_path)
    assert plan and plan[0].kind == "move"
    (tmp_path / "gguf" / "pub" / "Foo-GGUF").mkdir(parents=True)  # empty dir appears before apply

    assert library.apply_migration(plan) == (0, 0, 0)  # nothing moved, nothing deduped
    assert (hf / "m.gguf").read_bytes() == b"complete-weights"  # the only copy survives


def test_migrate_does_not_plan_a_dedup_against_an_empty_bucket_dir(tmp_path: Path) -> None:
    # Same measurement hole seen at plan time: an empty dir in the bucket must never be planned
    # as a "verified duplicate" that licenses deleting the huggingface/ copy.
    hf = _symlinked_hf_model(tmp_path)
    (tmp_path / "gguf" / "pub" / "Foo-GGUF").mkdir(parents=True)

    plan = library.plan_migration(tmp_path)
    assert plan == []  # unverified conflict: left alone entirely
    library.apply_migration(plan)
    assert (hf / "m.gguf").read_bytes() == b"complete-weights"


def test_migrate_dedup_reverifies_the_bucket_copy_before_deleting(tmp_path: Path) -> None:
    # The dedup branch re-verifies at apply time: if the bucket copy was emptied in between (a
    # concurrent remove), the huggingface/ copy is the last one and must not be deleted.
    hf = tmp_path / "huggingface" / "pub" / "Foo-GGUF"
    bucket = tmp_path / "gguf" / "pub" / "Foo-GGUF"
    _gguf(hf, "m.gguf", b"complete-weights")
    _gguf(bucket, "m.gguf", b"complete-weights")
    plan = library.plan_migration(tmp_path)
    assert plan and plan[0].kind == "dedup"
    (bucket / "m.gguf").unlink()  # bucket copy gutted between plan and apply

    assert library.apply_migration(plan) == (0, 0, 0)
    assert (hf / "m.gguf").read_bytes() == b"complete-weights"


# --- A3: ModelRef identity + per-item fault isolation in scan ---


def test_modelref_is_a_hashable_identity() -> None:
    # A model is identified by (name, format): same name+format are equal + hash together; a GGUF
    # vs an MLX build of the same repo are distinct. Frozen/hashable so it can key the scan dedup.
    a = library.ModelRef(name="pub/x", model_format=ModelFormat.gguf)
    b = library.ModelRef(name="pub/x", model_format=ModelFormat.gguf)
    c = library.ModelRef(name="pub/x", model_format=ModelFormat.mlx)
    assert a == b and hash(a) == hash(b)
    assert a != c
    seen: set[library.ModelRef] = set()  # as scan() keys its dedup
    for ref in (a, b, c):
        seen.add(ref)
    assert len(seen) == 2  # dedups by identity (a==b collapse; c distinct)
    m = library.LibraryModel(name="pub/x", model_format=ModelFormat.gguf, path=Path("/x"), load_target=Path("/x"))
    assert m.ref == a  # LibraryModel exposes its own identity


def test_isolated_skips_failing_and_none_items() -> None:
    def build(n: int) -> library.LibraryModel | None:
        if n == 2:
            raise ValueError("corrupt")  # a per-item failure must not propagate
        if n == 3:
            return None  # a deliberate skip
        return library.LibraryModel(
            name=f"m{n}", model_format=ModelFormat.gguf, path=Path("/x"), load_target=Path("/x")
        )

    out = library._scan._isolated(build, [1, 2, 3, 4])
    assert [m.name for m in out] == ["m1", "m4"]  # 2 (raised) and 3 (None) both dropped, rest survive


def test_scan_survives_a_corrupt_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # One unreadable model on disk must not crash the whole library listing (A3): scan returns the
    # healthy models and silently skips the broken one.
    _gguf(tmp_path / "gguf" / "pub" / "good")
    _gguf(tmp_path / "gguf" / "pub" / "bad")
    real = library._scan._model_from_dir

    def faulty(model_dir: Path, base: Path) -> library.LibraryModel | None:
        if model_dir.name == "bad":
            raise ValueError("simulated corruption")
        return real(model_dir, base)

    monkeypatch.setattr(library._scan, "_model_from_dir", faulty)
    names = {m.name for m in library.scan(root=tmp_path)}
    assert "pub/good" in names  # healthy model survives
    assert "pub/bad" not in names  # corrupt one skipped, no exception
