"""Tests for `heim library verify` — on-disk integrity checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from heim import library
from heim.models import ModelFormat
from heim.sources.base import dir_stats


def _gguf_model(tmp_path: Path, *, empty: bool = False, with_card: bool = True) -> library.LibraryModel:
    repo = tmp_path / "gguf" / "pub" / "Foo-GGUF"
    repo.mkdir(parents=True)
    gguf = repo / "foo-Q4_K_M.gguf"
    gguf.write_bytes(b"" if empty else b"weights")
    if with_card:
        (repo / "README.md").write_text("# card")
    sidecar = repo / ".heim"
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
    (model.path / ".heim" / "metadata.json").write_text('{"card": "README.md"}')
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
