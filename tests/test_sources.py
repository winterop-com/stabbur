"""Tests for the source-store adapters against synthetic stores."""

import json
from pathlib import Path

import pytest

from kodo import library, runtime
from kodo.config import Settings
from kodo.models import ModelFormat, ModelSource
from kodo.sources import base, lmstudio, ollama


def _make_ollama_store(root: Path) -> None:
    """Create a minimal Ollama-style store with one model:tag and a blob."""
    blob_digest = "sha256:abc123"
    blob = root / "blobs" / blob_digest.replace(":", "-")
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"x" * 2048)

    manifest = root / "manifests" / "registry.ollama.ai" / "library" / "llama3" / "latest"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"layers": [{"digest": blob_digest, "size": 2048}]}))


def test_ollama_list_and_backup(tmp_path: Path) -> None:
    store = tmp_path / "ollama"
    _make_ollama_store(store)

    entries = ollama.list_models(models_dir=store)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.source is ModelSource.ollama
    assert entry.name == "llama3:latest"
    assert entry.size_bytes == 2048

    library_root = tmp_path / "backup"
    result = ollama.pull("llama3:latest", library_root, models_dir=store)
    assert result.file_count == 2  # manifest + blob
    assert (library_root / "ollama" / "blobs" / "sha256-abc123").is_file()


def _write_ollama_manifest(store: Path, model: str, digests: list[str]) -> None:
    """Write a manifest under library/<model>/latest referencing the digests."""
    manifest = store / "manifests" / "registry.ollama.ai" / "library" / model / "latest"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"layers": [{"digest": d} for d in digests]}))


def test_ollama_move_preserves_shared_blobs(tmp_path: Path) -> None:
    store = tmp_path / "ollama"
    (store / "blobs").mkdir(parents=True)
    (store / "blobs" / "sha256-shared").write_bytes(b"s" * 100)
    (store / "blobs" / "sha256-uniq").write_bytes(b"u" * 200)
    _write_ollama_manifest(store, "modelA", ["sha256:shared", "sha256:uniq"])
    _write_ollama_manifest(store, "modelB", ["sha256:shared"])

    library_root = tmp_path / "backup"
    ollama.pull("modelA:latest", library_root, models_dir=store, move=True)

    # modelA gone; its unique blob gone; shared blob kept (modelB still needs it).
    assert not (store / "manifests" / "registry.ollama.ai" / "library" / "modelA").exists()
    assert not (store / "blobs" / "sha256-uniq").exists()
    assert (store / "blobs" / "sha256-shared").is_file()
    assert (store / "manifests" / "registry.ollama.ai" / "library" / "modelB" / "latest").is_file()
    assert (library_root / "ollama" / "blobs" / "sha256-uniq").is_file()


def test_ollama_pull_missing_blob_raises(tmp_path: Path) -> None:
    # Manifest references a blob that is not in the store: the source is corrupt,
    # so pull must fail loudly rather than write a partial (unrestorable) backup.
    store = tmp_path / "ollama"
    (store / "blobs").mkdir(parents=True)
    _write_ollama_manifest(store, "broken", ["sha256:gone"])

    library_root = tmp_path / "backup"
    with pytest.raises(FileNotFoundError, match="missing from the store"):
        ollama.pull("broken:latest", library_root, models_dir=store)

    # Nothing must be written to the backup — no manifest pointing at absent content.
    assert not (library_root / "ollama").exists()


@pytest.mark.parametrize("body", ["{not valid json", json.dumps({"layers": []})])
def test_ollama_pull_corrupt_manifest_raises_and_keeps_source(tmp_path: Path, body: str) -> None:
    # An unreadable manifest, or one with no blobs, is not a restorable model: pull
    # must fail (and --move must not delete the only source copy), not report success.
    store = tmp_path / "ollama"
    (store / "blobs").mkdir(parents=True)
    manifest = store / "manifests" / "registry.ollama.ai" / "library" / "corrupt" / "latest"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(body)
    library_root = tmp_path / "backup"

    with pytest.raises(ValueError, match="unreadable|no blobs"):
        ollama.pull("corrupt:latest", library_root, models_dir=store, move=True)

    assert manifest.exists()  # --move must not have deleted a corrupt source
    assert not (library_root / "ollama").exists()


def test_ollama_pull_manifest_not_published_if_blob_copy_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A copy failure (disk full / drive unplugged) after prevalidation must not
    # leave a manifest referencing content that never finished copying.
    store = tmp_path / "ollama"
    (store / "blobs").mkdir(parents=True)
    (store / "blobs" / "sha256-a1").write_bytes(b"w" * 64)
    _write_ollama_manifest(store, "alpha", ["sha256:a1"])
    library_root = tmp_path / "backup"

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(ollama.shutil, "copy2", _boom)
    with pytest.raises(OSError, match="disk full"):
        ollama.pull("alpha:latest", library_root, models_dir=store)

    # No manifest published, and no leftover partial blob.
    assert not (library_root / "ollama" / "manifests").exists()
    leftover = list((library_root / "ollama" / "blobs").glob(".*.partial"))
    assert leftover == []


def test_copy_tree_preserves_old_backup_and_cleans_staging_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A real mid-copy failure (staging partially populated) must leave the existing
    # backup intact AND leave no staging residue consuming disk.
    src = tmp_path / "src"
    src.mkdir()
    (src / "new.bin").write_bytes(b"n" * 100)
    dest = tmp_path / "lib" / "repo"
    dest.mkdir(parents=True)
    (dest / "old.bin").write_bytes(b"o" * 200)  # the previous good backup

    def _boom(_src: str, dst: str, **_: object) -> None:
        Path(dst).mkdir(parents=True)  # simulate copytree populating staging...
        (Path(dst) / "half.bin").write_bytes(b"x" * 10)
        raise OSError("disk full mid-copy")  # ...then failing partway

    monkeypatch.setattr(base.shutil, "copytree", _boom)
    with pytest.raises(OSError, match="disk full"):
        base.copy_tree(src, dest)

    # Old backup survives untouched; no staging dir left behind anywhere under parent.
    assert (dest / "old.bin").read_bytes() == b"o" * 200
    assert [p.name for p in dest.parent.iterdir()] == ["repo"]


def test_copy_tree_does_not_clobber_sibling_in_model_namespace(tmp_path: Path) -> None:
    # A real model named like the old temp suffix must not be destroyed by a pull.
    src = tmp_path / "src"
    src.mkdir()
    (src / "w.bin").write_bytes(b"n" * 100)
    dest = tmp_path / "lib" / "Foo"
    sibling = tmp_path / "lib" / "Foo.partial"  # a legitimate model, not our temp
    sibling.mkdir(parents=True)
    (sibling / "keep.bin").write_bytes(b"k" * 50)

    base.copy_tree(src, dest)

    assert (dest / "w.bin").is_file()
    assert (sibling / "keep.bin").read_bytes() == b"k" * 50  # untouched


def test_lmstudio_gguf_list_and_backup(tmp_path: Path) -> None:
    store = tmp_path / "lmstudio"
    model_dir = store / "TheBloke" / "Mistral-7B-GGUF"
    model_dir.mkdir(parents=True)
    (model_dir / "mistral.Q4_K_M.gguf").write_bytes(b"y" * 4096)

    entries = lmstudio.list_models(models_dir=store)
    assert len(entries) == 1
    assert entries[0].name == "TheBloke/Mistral-7B-GGUF"
    assert entries[0].model_format is ModelFormat.gguf
    assert entries[0].size_bytes == 4096

    library_root = tmp_path / "backup"
    result = lmstudio.pull("TheBloke/Mistral-7B-GGUF", library_root, models_dir=store)
    assert result.model_format is ModelFormat.gguf
    assert result.size_bytes == 4096
    assert (library_root / "gguf" / "TheBloke" / "Mistral-7B-GGUF" / "mistral.Q4_K_M.gguf").is_file()


def test_lmstudio_mlx_detected_and_backed_up(tmp_path: Path) -> None:
    store = tmp_path / "lmstudio"
    model_dir = store / "mlx-community" / "Qwen-MLX-4bit"
    model_dir.mkdir(parents=True)
    (model_dir / "model.safetensors").write_bytes(b"z" * 8192)
    (model_dir / "config.json").write_text("{}")

    entries = lmstudio.list_models(models_dir=store)
    assert len(entries) == 1
    assert entries[0].model_format is ModelFormat.mlx

    library_root = tmp_path / "backup"
    result = lmstudio.pull("mlx-community/Qwen-MLX-4bit", library_root, models_dir=store)
    assert result.model_format is ModelFormat.mlx
    assert (library_root / "mlx" / "mlx-community" / "Qwen-MLX-4bit" / "model.safetensors").is_file()


def test_lmstudio_backup_move_removes_source(tmp_path: Path) -> None:
    store = tmp_path / "lmstudio"
    model_dir = store / "pub" / "Model-GGUF"
    model_dir.mkdir(parents=True)
    (model_dir / "model.gguf").write_bytes(b"w" * 4096)

    library_root = tmp_path / "backup"
    result = lmstudio.pull("pub/Model-GGUF", library_root, models_dir=store, move=True)

    assert (library_root / "gguf" / "pub" / "Model-GGUF" / "model.gguf").is_file()
    assert not model_dir.exists()  # local source removed after verified copy
    assert result.size_bytes == 4096


def _make_library(root: Path) -> None:
    """Create a synthetic library: one GGUF and one MLX model."""
    gguf = root / "gguf" / "pub" / "Model-GGUF"
    gguf.mkdir(parents=True)
    (gguf / "model.Q4_K_M.gguf").write_bytes(b"g" * 1024)

    mlx = root / "mlx" / "pub" / "Model-MLX"
    mlx.mkdir(parents=True)
    (mlx / "model.safetensors").write_bytes(b"m" * 2048)
    (mlx / "config.json").write_text("{}")


def test_library_scan_and_find(tmp_path: Path) -> None:
    _make_library(tmp_path)

    models = library.scan(root=tmp_path)
    assert {m.model_format for m in models} == {ModelFormat.gguf, ModelFormat.mlx}

    # Bare repo name matches; format filter disambiguates.
    assert len(library.find("Model-GGUF", root=tmp_path)) == 1
    assert library.find("Model-MLX", root=tmp_path)[0].model_format is ModelFormat.mlx
    assert library.find("missing", root=tmp_path) == []


def test_runtime_build_command(tmp_path: Path) -> None:
    _make_library(tmp_path)
    gguf = library.find("Model-GGUF", root=tmp_path)[0]
    mlx = library.find("Model-MLX", root=tmp_path)[0]

    gguf_cmd = runtime.build_command(gguf, "127.0.0.1", 8080)
    assert gguf_cmd[0] == "llama-server"
    assert gguf_cmd[-1] == "8080"
    assert str(gguf.path / "model.Q4_K_M.gguf") in gguf_cmd

    mlx_cmd = runtime.build_command(mlx, "127.0.0.1", 8081)
    assert mlx_cmd[0] == "mlx_lm.server"
    assert str(mlx.path) in mlx_cmd


def test_build_command_refuses_safetensors(tmp_path: Path) -> None:
    # safetensors is a convert/fine-tune source, not a runnable build: mlx_lm
    # cannot serve it, so building a command must fail with a clear message.
    model = library.LibraryModel(
        name="pub/Full",
        model_format=ModelFormat.safetensors,
        generative=True,
        is_ollama=False,
        path=tmp_path,
        load_target=tmp_path,
        mmproj=None,
        size_bytes=0,
    )
    with pytest.raises(ValueError, match="not directly runnable"):
        runtime.build_command(model, "127.0.0.1", 8080)


def test_scan_keeps_cross_root_format_variants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Same repo, different formats across roots: a GGUF on the drive and an MLX
    # copy locally must both survive dedup so --format can disambiguate them.
    drive, local = tmp_path / "drive", tmp_path / "local"
    (drive / "gguf" / "pub" / "Foo").mkdir(parents=True)
    (drive / "gguf" / "pub" / "Foo" / "m.gguf").write_bytes(b"g" * 100)
    (local / "mlx" / "pub" / "Foo").mkdir(parents=True)
    (local / "mlx" / "pub" / "Foo" / "weights.safetensors").write_bytes(b"m" * 100)

    settings = Settings(library_root=drive, local_root=local)
    monkeypatch.setattr(library, "get_settings", lambda: settings)
    formats = {m.model_format for m in library.find("Foo")}
    assert formats == {ModelFormat.gguf, ModelFormat.mlx}
    assert len(library.find("Foo", model_format=ModelFormat.mlx)) == 1


def test_library_finds_huggingface_and_ignores_appledouble(tmp_path: Path) -> None:
    hf = tmp_path / "huggingface" / "unsloth" / "X-GGUF"
    hf.mkdir(parents=True)
    (hf / "X-F16.gguf").write_bytes(b"f" * 4000)  # bigger, but not preferred
    (hf / "X-Q4_K_M.gguf").write_bytes(b"q" * 1000)  # preferred quant
    (hf / "._X-Q4_K_M.gguf").write_bytes(b"junk")  # macOS AppleDouble — must be ignored

    models = library.scan(root=tmp_path)
    assert len(models) == 1
    m = models[0]
    assert m.name == "unsloth/X-GGUF"  # leading 'huggingface' prefix stripped
    assert m.model_format is ModelFormat.gguf
    assert m.load_target.name == "X-Q4_K_M.gguf"  # prefers Q4_K_M, not F16, not ._


def test_library_finds_ollama_native(tmp_path: Path) -> None:
    store = tmp_path / "ollama"
    (store / "blobs").mkdir(parents=True)
    (store / "blobs" / "sha256-model").write_bytes(b"g" * 5000)
    man = store / "manifests" / "registry.ollama.ai" / "library" / "foo" / "latest"
    man.parent.mkdir(parents=True)
    man.write_text(
        json.dumps({"layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:model"}]})
    )

    models = library.scan(root=tmp_path)
    assert len(models) == 1
    assert models[0].name == "foo:latest"
    assert models[0].model_format is ModelFormat.gguf
    assert models[0].load_target.name == "sha256-model"


def test_scan_spans_drive_and_local_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    drive, local = tmp_path / "drive", tmp_path / "local"
    for root_dir, nm in ((drive, "Drive-GGUF"), (local, "Local-GGUF")):
        d = root_dir / "gguf" / "pub" / nm
        d.mkdir(parents=True)
        (d / "m.gguf").write_bytes(b"x" * 100)

    settings = Settings(library_root=drive, local_root=local)
    monkeypatch.setattr(library, "get_settings", lambda: settings)
    names = {m.name for m in library.scan()}  # no explicit root → spans both
    assert names == {"pub/Drive-GGUF", "pub/Local-GGUF"}


def test_serves_web_ui(tmp_path: Path) -> None:
    _make_library(tmp_path)
    gguf = library.find("Model-GGUF", root=tmp_path)[0]
    mlx = library.find("Model-MLX", root=tmp_path)[0]
    assert runtime.serves_web_ui(gguf) is True
    assert runtime.serves_web_ui(mlx) is False
