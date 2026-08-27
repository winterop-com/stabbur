"""Tests for `stabbur library verify` — on-disk integrity checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stabbur import cli, library
from stabbur import library as library_ops
from stabbur.models import ModelFormat
from stabbur.sources.base import dir_stats

runner = CliRunner()


def _record(model: library.LibraryModel, **fields: object) -> None:
    """Overwrite the model's recorded stats, keeping the rest of the sidecar."""
    path = model.path / ".stabbur" / "metadata.json"
    path.write_text(json.dumps(json.loads(path.read_text()) | fields))


def _gguf_model(tmp_path: Path, *, empty: bool = False, with_card: bool = True) -> library.LibraryModel:
    repo = tmp_path / "gguf" / "pub" / "Foo-GGUF"
    repo.mkdir(parents=True)
    gguf = repo / "foo-Q4_K_M.gguf"
    gguf.write_bytes(b"" if empty else b"weights")
    if with_card:
        (repo / "README.md").write_text("# card")
    sidecar = repo / ".stabbur"
    sidecar.mkdir()
    # Record the stats a real pull would: measured with dir_stats, after the files are in place.
    size, files = dir_stats(repo)
    (sidecar / "metadata.json").write_text(f'{{"card": "README.md", "size_bytes": {size}, "file_count": {files}}}')
    return library.LibraryModel(name="pub/Foo-GGUF", model_format=ModelFormat.gguf, path=repo, load_target=gguf)


def test_verify_ok_when_weights_and_card_present(tmp_path: Path) -> None:
    result = library.verify(_gguf_model(tmp_path))
    assert result.ok
    assert result.checked == "weights+size+card"
    assert result.issues == []


def test_verify_flags_truncated_weights(tmp_path: Path) -> None:
    # The whole point of the size check: the file still exists and is non-empty, so every
    # structural check passes — only the recorded size reveals the pull stopped partway.
    model = _gguf_model(tmp_path)
    model.load_target.write_bytes(b"w")
    result = library.verify(model)
    assert not result.ok
    assert any("size" in i and "recorded" in i for i in result.issues)


def test_verify_flags_an_extra_or_missing_file(tmp_path: Path) -> None:
    model = _gguf_model(tmp_path)
    (model.path / "extra.gguf").write_bytes(b"surprise")
    result = library.verify(model)
    assert not result.ok
    assert any("files != recorded" in i for i in result.issues)


def test_verify_skips_the_size_check_when_the_sidecar_never_recorded_it(tmp_path: Path) -> None:
    # Older pulls wrote no size_bytes; they must not all report as damaged.
    model = _gguf_model(tmp_path)
    (model.path / ".stabbur" / "metadata.json").write_text('{"card": "README.md"}')
    model.load_target.write_bytes(b"much shorter than recorded")
    result = library.verify(model)
    assert result.ok
    assert result.checked == "weights+card"


def test_verify_flags_missing_weights(tmp_path: Path) -> None:
    model = _gguf_model(tmp_path)
    model.load_target.unlink()
    result = library.verify(model)
    assert not result.ok
    assert any("missing" in i for i in result.issues)


def test_verify_flags_empty_weight_file(tmp_path: Path) -> None:
    result = library.verify(_gguf_model(tmp_path, empty=True))
    assert not result.ok
    assert any("empty" in i for i in result.issues)


def test_verify_flags_missing_card(tmp_path: Path) -> None:
    model = _gguf_model(tmp_path)
    (model.path / "README.md").unlink()  # metadata still names it
    result = library.verify(model)
    assert not result.ok
    assert any("card" in i for i in result.issues)


def test_verify_flags_missing_projector(tmp_path: Path) -> None:
    model = _gguf_model(tmp_path)
    model = model.model_copy(update={"mmproj": model.path / "mmproj.gguf"})  # declared but absent
    result = library.verify(model)
    assert not result.ok
    assert any("projector" in i for i in result.issues)


def test_verify_mlx_needs_safetensors(tmp_path: Path) -> None:
    repo = tmp_path / "mlx" / "mlx-community" / "Bar-4bit"
    repo.mkdir(parents=True)
    (repo / "config.json").write_text("{}")  # a dir with no .safetensors
    model = library.LibraryModel(
        name="mlx-community/Bar-4bit", model_format=ModelFormat.mlx, path=repo, load_target=repo
    )
    result = library.verify(model)
    assert not result.ok
    assert any("safetensors" in i for i in result.issues)
    # add a real weight → ok
    (repo / "model.safetensors").write_bytes(b"w")
    assert library.verify(model).ok


def test_size_mismatch_falls_back_to_bytes_when_both_sides_round_the_same() -> None:
    # A 45 KB gap in a 16 GB model rendered as "size 16.3 GB != recorded 16.3 GB" — a line that
    # reads as a bug in stabbur, not a finding about the model.
    on_disk, recorded = 17_478_546_744, 17_478_592_341
    message = library._manage._size_mismatch(on_disk, recorded)
    assert message == f"size {on_disk:,} bytes != recorded {recorded:,} bytes"
    # A gap the human sizes do show keeps the readable form.
    assert library._manage._size_mismatch(17_478_546_744, 26_400_000_000) == "size 16.3 GB != recorded 24.6 GB"


def _bulky_gguf_model(tmp_path: Path, weight_bytes: int = 4_000_000) -> library.LibraryModel:
    """A model big enough that a bookkeeping-sized delta is a rounding error against it."""
    model = _gguf_model(tmp_path)
    model.load_target.write_bytes(b"w" * weight_bytes)
    size, files = dir_stats(model.path)
    _record(model, size_bytes=size, file_count=files)
    return model


def test_verify_notes_a_stale_recorded_count_instead_of_failing(tmp_path: Path) -> None:
    # Sidecars written before dir_stats excluded bookkeeping (.cache/, .stabbur/, ._ files)
    # recorded those in their totals, so a healthy old pull measures a few small files short.
    # That is how the two sidecars counted, not damage — it must not fail a healthy drive.
    model = _bulky_gguf_model(tmp_path)
    size, files = dir_stats(model.path)
    _record(model, size_bytes=size + 45_597, file_count=files + 16)
    result = library.verify(model)
    assert result.ok
    assert result.issues == []
    assert "counted differently" in result.notes[0]
    assert result.checked == "weights+size+card"


def test_verify_notes_a_count_difference_with_identical_bytes(tmp_path: Path) -> None:
    # The narrowest case: every byte accounted for, only the file tally is off.
    model = _gguf_model(tmp_path)
    size, files = dir_stats(model.path)
    _record(model, size_bytes=size, file_count=files + 3)
    result = library.verify(model)
    assert result.ok
    assert result.notes and not result.issues


def test_verify_still_fails_a_shortfall_too_big_to_be_bookkeeping(tmp_path: Path) -> None:
    # The note class is bounded: bookkeeping files are tiny, so a shortfall of megabytes per
    # missing file entry is damage, however the counts differ.
    model = _bulky_gguf_model(tmp_path)
    size, files = dir_stats(model.path)
    _record(model, size_bytes=size + 8 * (1 << 20), file_count=files + 1)
    result = library.verify(model)
    assert not result.ok
    assert result.notes == []


def test_verify_still_fails_when_the_missing_bytes_are_most_of_the_model(tmp_path: Path) -> None:
    # The per-file bound alone would excuse a tiny model losing nearly all of itself across a
    # handful of entries; the second bound (a share of the recorded total) is what refuses that.
    model = _gguf_model(tmp_path)  # a few bytes of weight
    size, files = dir_stats(model.path)
    _record(model, size_bytes=size + 45_797, file_count=files + 16)
    result = library.verify(model)
    assert not result.ok
    assert result.notes == []


def test_verify_still_fails_a_truncated_weight_with_an_intact_file_count(tmp_path: Path) -> None:
    # A stopped download shortens a file; it never removes one. The count is unchanged, so the
    # note class is out of reach and the size check fires as loudly as before.
    model = _gguf_model(tmp_path)
    size, files = dir_stats(model.path)
    _record(model, size_bytes=size + 5_000_000_000, file_count=files)
    result = library.verify(model)
    assert not result.ok
    assert any("recorded" in i for i in result.issues)


def test_verify_cli_exits_zero_for_a_stale_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The whole point: a healthy drive whose old sidecars counted differently must not exit 1.
    model = _bulky_gguf_model(tmp_path)
    size, files = dir_stats(model.path)
    _record(model, size_bytes=size + 4_000, file_count=files + 9)
    monkeypatch.setattr(library_ops, "scan", lambda: [model])
    result = runner.invoke(cli.app, ["library", "verify"])
    assert result.exit_code == 0, result.output
    assert "counted differently" in result.output
    assert "stale recorded counts" in result.output


def test_verify_ollama_uses_blob_digests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "ollama" / "manifests" / "m"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    model = library.LibraryModel(
        name="gemma3:latest",
        model_format=ModelFormat.gguf,
        is_ollama=True,
        path=manifest,
        load_target=manifest,
        library_root=tmp_path,
    )
    monkeypatch.setattr(library._manage.ollama, "verify_manifest", lambda *a, **k: (["missing blob sha256:abc"], 3))
    result = library.verify(model, deep=True)
    assert not result.ok
    assert result.checked == "blobs+sha256 (3)"
    assert result.issues == ["missing blob sha256:abc"]
