"""Tests for the source-store adapters against synthetic stores."""

import json
from pathlib import Path

from kodo import library, runtime
from kodo.models import ModelFormat, ModelSource
from kodo.sources import lmstudio, ollama


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

    backup_root = tmp_path / "backup"
    result = ollama.pull("llama3:latest", backup_root, models_dir=store)
    assert result.file_count == 2  # manifest + blob
    assert (backup_root / "ollama" / "blobs" / "sha256-abc123").is_file()


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

    backup_root = tmp_path / "backup"
    ollama.pull("modelA:latest", backup_root, models_dir=store, move=True)

    # modelA gone; its unique blob gone; shared blob kept (modelB still needs it).
    assert not (store / "manifests" / "registry.ollama.ai" / "library" / "modelA").exists()
    assert not (store / "blobs" / "sha256-uniq").exists()
    assert (store / "blobs" / "sha256-shared").is_file()
    assert (store / "manifests" / "registry.ollama.ai" / "library" / "modelB" / "latest").is_file()
    assert (backup_root / "ollama" / "blobs" / "sha256-uniq").is_file()


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

    backup_root = tmp_path / "backup"
    result = lmstudio.pull("TheBloke/Mistral-7B-GGUF", backup_root, models_dir=store)
    assert result.model_format is ModelFormat.gguf
    assert result.size_bytes == 4096
    assert (backup_root / "gguf" / "TheBloke" / "Mistral-7B-GGUF" / "mistral.Q4_K_M.gguf").is_file()


def test_lmstudio_mlx_detected_and_backed_up(tmp_path: Path) -> None:
    store = tmp_path / "lmstudio"
    model_dir = store / "mlx-community" / "Qwen-MLX-4bit"
    model_dir.mkdir(parents=True)
    (model_dir / "model.safetensors").write_bytes(b"z" * 8192)
    (model_dir / "config.json").write_text("{}")

    entries = lmstudio.list_models(models_dir=store)
    assert len(entries) == 1
    assert entries[0].model_format is ModelFormat.mlx

    backup_root = tmp_path / "backup"
    result = lmstudio.pull("mlx-community/Qwen-MLX-4bit", backup_root, models_dir=store)
    assert result.model_format is ModelFormat.mlx
    assert (backup_root / "mlx" / "mlx-community" / "Qwen-MLX-4bit" / "model.safetensors").is_file()


def test_lmstudio_backup_move_removes_source(tmp_path: Path) -> None:
    store = tmp_path / "lmstudio"
    model_dir = store / "pub" / "Model-GGUF"
    model_dir.mkdir(parents=True)
    (model_dir / "model.gguf").write_bytes(b"w" * 4096)

    backup_root = tmp_path / "backup"
    result = lmstudio.pull("pub/Model-GGUF", backup_root, models_dir=store, move=True)

    assert (backup_root / "gguf" / "pub" / "Model-GGUF" / "model.gguf").is_file()
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


def test_runtime_chat_command(tmp_path: Path) -> None:
    _make_library(tmp_path)
    gguf = library.find("Model-GGUF", root=tmp_path)[0]
    mlx = library.find("Model-MLX", root=tmp_path)[0]

    assert runtime.build_chat_command(gguf)[0] == "llama-cli"
    assert "--conversation" in runtime.build_chat_command(gguf)
    assert runtime.build_chat_command(mlx)[0] == "mlx_lm.chat"

    assert runtime.serves_web_ui(gguf) is True
    assert runtime.serves_web_ui(mlx) is False
