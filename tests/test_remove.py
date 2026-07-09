"""Tests for removing models from the library (directory + Ollama delegation)."""

import hashlib
import json
from pathlib import Path

from kodo import library, tags
from kodo.models import ModelFormat


def test_remove_directory_model_deletes_the_dir(tmp_path: Path) -> None:
    d = tmp_path / "gguf" / "pub" / "repo"
    d.mkdir(parents=True)
    (d / "model.gguf").write_bytes(b"x" * 100)
    m = library.LibraryModel(
        name="pub/repo",
        model_format=ModelFormat.gguf,
        path=d,
        load_target=d / "model.gguf",
        size_bytes=100,
        file_count=1,
    )
    count, freed = library.remove(m)
    assert not d.exists()
    assert (count, freed) == (1, 100)


def _gguf_model(root: Path) -> library.LibraryModel:
    d = root / "gguf" / "pub" / "repo"
    d.mkdir(parents=True)
    (d / "model.gguf").write_bytes(b"x" * 100)
    return library.LibraryModel(
        name="pub/repo",
        model_format=ModelFormat.gguf,
        path=d,
        load_target=d / "model.gguf",
        size_bytes=100,
        file_count=1,
        library_root=root,
    )


def test_remove_keeps_tags_when_another_format_copy_survives(tmp_path: Path) -> None:
    # Tags are keyed by name alone while identity is (name, format): removing the GGUF
    # copy must NOT drop the tags off the surviving safetensors copy of the same name.
    m = _gguf_model(tmp_path)
    s = tmp_path / "safetensors" / "pub" / "repo"
    s.mkdir(parents=True)
    (s / "model.safetensors").write_bytes(b"y" * 100)
    (s / "config.json").write_text("{}")
    tags.set_tags(tmp_path, "pub/repo", ["favorite"])

    library.remove(m)

    assert not m.path.exists()
    assert tags.tags_for(tmp_path, "pub/repo") == ["favorite"]  # surviving copy keeps them


def test_remove_drops_tags_with_the_last_copy(tmp_path: Path) -> None:
    # No other format copy left -> the tags go too, so a later re-pull starts clean.
    m = _gguf_model(tmp_path)
    tags.set_tags(tmp_path, "pub/repo", ["favorite"])

    library.remove(m)

    assert tags.tags_for(tmp_path, "pub/repo") == []


def _add_blob(store: Path, data: bytes) -> str:
    hexd = hashlib.sha256(data).hexdigest()
    blobs = store / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    (blobs / f"sha256-{hexd}").write_bytes(data)
    return f"sha256:{hexd}"


def _write_manifest(store: Path, model: str, digests: list[str]) -> Path:
    man = store / "manifests" / "registry.ollama.ai" / "library" / model / "latest"
    man.parent.mkdir(parents=True, exist_ok=True)
    body = {"config": {"digest": digests[0]}, "layers": [{"digest": d} for d in digests[1:]]}
    man.write_text(json.dumps(body))
    return man


def test_remove_ollama_model_via_library(tmp_path: Path) -> None:
    # library.remove derives the Ollama store from the manifest path and delegates
    # to ollama.remove, which keeps blobs still shared with another model.
    store = tmp_path / "ollama"
    shared = _add_blob(store, b"s" * 100)
    uniq = _add_blob(store, b"u" * 60)
    man_a = _write_manifest(store, "modelA", [uniq, shared])
    _write_manifest(store, "modelB", [shared])  # modelB still needs the shared blob

    m = library.LibraryModel(
        name="modelA:latest",
        model_format=ModelFormat.gguf,
        is_ollama=True,
        path=man_a,
        load_target=man_a,
        size_bytes=60,
        file_count=1,
    )
    count, freed = library.remove(m)

    assert not man_a.exists()  # manifest removed
    assert not (store / "blobs" / uniq.replace(":", "-")).exists()  # unique blob gone
    assert (store / "blobs" / shared.replace(":", "-")).is_file()  # shared blob kept
    assert (count, freed) == (1, 60)


def test_remove_ollama_deletes_the_library_sidecar(tmp_path: Path) -> None:
    # pull writes a browsable sidecar under ollama/.library/<safe_name>/; remove must delete it
    # too, not orphan it.
    from kodo.sources import ollama

    store = tmp_path / "ollama"
    digest = _add_blob(store, b"z" * 40)
    man = _write_manifest(store, "solo", [digest])
    sidecar = store / ".library" / ollama._safe_name("solo:latest")
    sidecar.mkdir(parents=True)
    (sidecar / "metadata.json").write_text("{}")
    (sidecar / "model-card.md").write_text("# solo")

    ollama.remove("solo:latest", store)

    assert not man.exists()
    assert not sidecar.exists()  # the sidecar is gone, not orphaned
