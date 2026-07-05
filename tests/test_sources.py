"""Tests for the source-store adapters against synthetic stores."""

import hashlib
import json
from pathlib import Path

import pytest

from kodo import catalog, library, runtime
from kodo.config import Settings
from kodo.models import ModelFormat, ModelSource
from kodo.sources import base, huggingface, lmstudio, ollama


def _add_blob(store: Path, content: bytes) -> str:
    """Write a content-addressed Ollama blob and return its ``sha256:<hex>`` digest."""
    hex_ = hashlib.sha256(content).hexdigest()
    (store / "blobs").mkdir(parents=True, exist_ok=True)
    (store / "blobs" / f"sha256-{hex_}").write_bytes(content)
    return f"sha256:{hex_}"


def _blob_name(digest: str) -> str:
    """The on-disk blob filename for a ``sha256:<hex>`` digest."""
    return digest.replace(":", "-")


def _make_ollama_store(root: Path) -> str:
    """Create a minimal Ollama-style store with one model:tag; return the blob digest."""
    digest = _add_blob(root, b"x" * 2048)
    manifest = root / "manifests" / "registry.ollama.ai" / "library" / "llama3" / "latest"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"layers": [{"digest": digest, "size": 2048}]}))
    return digest


def test_ollama_list_and_backup(tmp_path: Path) -> None:
    store = tmp_path / "ollama"
    digest = _make_ollama_store(store)

    entries = ollama.list_models(models_dir=store)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.source is ModelSource.ollama
    assert entry.name == "llama3:latest"
    assert entry.size_bytes == 2048

    library_root = tmp_path / "backup"
    result = ollama.pull("llama3:latest", library_root, models_dir=store)
    assert result.file_count == 2  # manifest + blob
    assert (library_root / "ollama" / "blobs" / _blob_name(digest)).is_file()


def _write_ollama_manifest(store: Path, model: str, digests: list[str]) -> None:
    """Write a manifest under library/<model>/latest referencing the digests."""
    manifest = store / "manifests" / "registry.ollama.ai" / "library" / model / "latest"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"layers": [{"digest": d} for d in digests]}))


def test_ollama_remove_preserves_blobs_when_other_manifest_corrupt(tmp_path: Path) -> None:
    # If a sibling manifest is unreadable, remove() can't know which blobs it needs,
    # so it must preserve blobs rather than risk deleting a shared layer.
    store = tmp_path / "ollama"
    shared = _add_blob(store, b"s" * 100)
    uniq = _add_blob(store, b"u" * 60)
    _write_ollama_manifest(store, "modelA", [shared, uniq])
    corrupt = store / "manifests" / "registry.ollama.ai" / "library" / "modelB" / "latest"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{not valid json")  # unreadable sibling that may reference 'shared'

    ollama.remove("modelA:latest", models_dir=store)

    assert not (store / "manifests" / "registry.ollama.ai" / "library" / "modelA").exists()
    assert (store / "blobs" / _blob_name(shared)).is_file()  # preserved (uncertain)
    assert (store / "blobs" / _blob_name(uniq)).is_file()  # preserved (uncertain)


def test_ollama_move_preserves_shared_blobs(tmp_path: Path) -> None:
    store = tmp_path / "ollama"
    shared = _add_blob(store, b"s" * 100)
    uniq = _add_blob(store, b"u" * 200)
    _write_ollama_manifest(store, "modelA", [shared, uniq])
    _write_ollama_manifest(store, "modelB", [shared])

    library_root = tmp_path / "backup"
    ollama.pull("modelA:latest", library_root, models_dir=store, move=True)

    # modelA gone; its unique blob gone; shared blob kept (modelB still needs it).
    assert not (store / "manifests" / "registry.ollama.ai" / "library" / "modelA").exists()
    assert not (store / "blobs" / _blob_name(uniq)).exists()
    assert (store / "blobs" / _blob_name(shared)).is_file()
    assert (store / "manifests" / "registry.ollama.ai" / "library" / "modelB" / "latest").is_file()
    assert (library_root / "ollama" / "blobs" / _blob_name(uniq)).is_file()


def test_ollama_pull_malformed_digest_raises(tmp_path: Path) -> None:
    # A digest that isn't sha256:<64 hex> must be rejected before it hits the path map.
    store = tmp_path / "ollama"
    (store / "blobs").mkdir(parents=True)
    _write_ollama_manifest(store, "bad", ["sha256:xyz"])
    with pytest.raises(ValueError, match="malformed"):
        ollama.pull("bad:latest", tmp_path / "backup", models_dir=store)


def test_ollama_pull_reverifies_corrupt_same_size_dest(tmp_path: Path) -> None:
    # A pre-existing dest blob of the SAME size but wrong content (content-addressed
    # digests make this detectable) must be re-copied, not trusted.
    store = tmp_path / "ollama"
    content = b"g" * 128
    digest = _add_blob(store, content)
    _write_ollama_manifest(store, "m", [digest])
    library_root = tmp_path / "backup"
    blobs = library_root / "ollama" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / _blob_name(digest)).write_bytes(b"Z" * 128)  # same size, wrong content

    ollama.pull("m:latest", library_root, models_dir=store)
    assert (blobs / _blob_name(digest)).read_bytes() == content  # re-copied correct content


def test_ollama_pull_missing_blob_raises(tmp_path: Path) -> None:
    # Manifest references a blob that is not in the store: the source is corrupt,
    # so pull must fail loudly rather than write a partial (unrestorable) backup.
    store = tmp_path / "ollama"
    (store / "blobs").mkdir(parents=True)
    _write_ollama_manifest(store, "broken", ["sha256:" + "0" * 64])  # well-formed but absent

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
    digest = _add_blob(store, b"w" * 64)
    _write_ollama_manifest(store, "alpha", [digest])
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


def test_runtime_early_exit_error_reports_cause(tmp_path: Path) -> None:
    # An early runtime exit surfaces the captured log tail and a port-in-use hint,
    # instead of the old opaque "exited before becoming ready".
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    (log_dir / "llama-server.log").write_text("E srv start: couldn't bind HTTP server socket, port: 8090\n")

    err = runtime._early_exit_error(["llama-server"], 1, log_dir, 8090)
    msg = str(err)
    assert "already in use" in msg  # friendly port hint
    assert "couldn't bind" in msg  # includes the runtime log tail


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


def test_mlx_detected_by_config_marker_outside_mlx_bucket(tmp_path: Path) -> None:
    # An MLX repo whose path gives no hint (e.g. lmstudio-community/, not mlx/) is still
    # classified as MLX via config.json's affine-quant marker, not misfiled as safetensors.
    model_dir = tmp_path / "lmstudio-community" / "Qwen-MLX-4bit"
    model_dir.mkdir(parents=True)
    (model_dir / "model.safetensors").write_bytes(b"z" * 2048)
    (model_dir / "config.json").write_text('{"quantization": {"group_size": 64, "bits": 4, "mode": "affine"}}')

    assert library._classify_dir(model_dir) is ModelFormat.mlx

    # A plain safetensors repo (HF-style quant, no affine marker) stays safetensors.
    plain = tmp_path / "some-publisher" / "Model-fp16"
    plain.mkdir(parents=True)
    (plain / "model.safetensors").write_bytes(b"z" * 2048)
    (plain / "config.json").write_text('{"quantization_config": {"quant_method": "gptq"}}')
    assert library._classify_dir(plain) is ModelFormat.safetensors


def test_library_scan_and_find(tmp_path: Path) -> None:
    _make_library(tmp_path)

    models = library.scan(root=tmp_path)
    assert {m.model_format for m in models} == {ModelFormat.gguf, ModelFormat.mlx}

    # Bare repo name matches; format filter disambiguates.
    assert len(library.find("Model-GGUF", root=tmp_path)) == 1
    assert library.find("Model-MLX", root=tmp_path)[0].model_format is ModelFormat.mlx
    assert library.find("missing", root=tmp_path) == []


def test_library_scans_voice_bucket_as_one_model(tmp_path: Path) -> None:
    _make_library(tmp_path)  # a GGUF + MLX chat LLM
    # A voice model under voice/<publisher>/<repo>, with a nested weights dir (Kokoro's
    # voices/) that must NOT be scanned as a separate model.
    v = tmp_path / "voice" / "mlx-community" / "Kokoro-82M-bf16"
    (v / "voices").mkdir(parents=True)
    (v / "model.safetensors").write_bytes(b"k" * 2048)
    (v / "voices" / "af.safetensors").write_bytes(b"a" * 512)
    # An unknown (not-in-registry) voice repo defaults to tts.
    unknown = tmp_path / "voice" / "acme" / "SomeTTS"
    unknown.mkdir(parents=True)
    (unknown / "model.safetensors").write_bytes(b"u" * 1024)

    models = library.scan(root=tmp_path)
    voices = [m for m in models if m.voice_kind]
    assert len(voices) == 2  # one per repo, not split by voices/
    kokoro = next(m for m in voices if m.name == "mlx-community/Kokoro-82M-bf16")
    assert kokoro.voice_kind == "tts" and not kokoro.generative and kokoro.tts
    assert next(m for m in voices if m.name == "acme/SomeTTS").voice_kind == "tts"  # default
    # Voice models never leak into the generative (chat) list.
    assert all(not m.voice_kind for m in models if m.generative)


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

    # n_ctx sets llama.cpp's context window (GGUF only); MLX ignores it.
    ctx_cmd = runtime.build_command(gguf, "127.0.0.1", 8080, n_ctx=4096)
    assert "-c" in ctx_cmd and ctx_cmd[ctx_cmd.index("-c") + 1] == "4096"
    assert "-c" not in runtime.build_command(mlx, "127.0.0.1", 8081, n_ctx=4096)
    assert "-c" not in runtime.build_command(gguf, "127.0.0.1", 8080)  # None → no flag


def test_build_command_routes_multimodal_mlx_to_vlm(tmp_path: Path) -> None:
    # A text-only MLX (no vision_config) serves via mlx_lm; a multimodal one
    # (vision_config present) must route to mlx-vlm, which mlx_lm can't load.
    text_dir = tmp_path / "mlx" / "pub" / "Text"
    text_dir.mkdir(parents=True)
    (text_dir / "model.safetensors").write_bytes(b"w")
    (text_dir / "config.json").write_text('{"architectures": ["LlamaForCausalLM"]}')

    vlm_dir = tmp_path / "mlx" / "pub" / "Vision"
    vlm_dir.mkdir(parents=True)
    (vlm_dir / "model.safetensors").write_bytes(b"w")
    (vlm_dir / "config.json").write_text('{"architectures": ["Gemma4ForConditionalGeneration"], "vision_config": {}}')

    text = library.find("Text", root=tmp_path)[0]
    vlm = library.find("Vision", root=tmp_path)[0]
    assert runtime.build_command(text, "127.0.0.1", 8080)[0] == "mlx_lm.server"
    assert runtime.build_command(vlm, "127.0.0.1", 8080)[0] == "mlx_vlm.server"


def test_hf_pull_include_sets_allow_patterns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # --include restricts the download to matching files, plus small sidecars, so
    # a single GGUF quant can be pulled from a multi-quant repo.
    captured: dict[str, object] = {}

    def fake_snapshot(**kwargs: object) -> str:
        captured.update(kwargs)
        return str(kwargs["local_dir"])

    monkeypatch.setattr(huggingface, "snapshot_download", fake_snapshot)
    monkeypatch.setattr(huggingface, "hub_format", lambda *a, **k: ModelFormat.gguf)  # no network

    huggingface.pull("pub/Repo-GGUF", tmp_path, include=["*Q4_K_M*", "*mmproj*"])
    patterns = captured["allow_patterns"]
    assert patterns == ["*Q4_K_M*", "*mmproj*", "*.md", "*.json", "*.txt"]

    huggingface.pull("pub/Repo", tmp_path)  # no include → no filtering
    assert captured["allow_patterns"] is None


def _fake_hfapi(files: list[str]) -> object:
    """An HfApi() stand-in whose list_repo_files returns ``files`` (no network)."""

    class _Api:
        def list_repo_files(self, repo_id: str, token: str | None = None) -> list[str]:
            return files

    return _Api()


def test_hub_format_detects_from_file_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(huggingface, "HfApi", lambda: _fake_hfapi(["model.gguf", "README.md"]))
    assert huggingface.hub_format("pub/Foo-GGUF") is ModelFormat.gguf

    monkeypatch.setattr(huggingface, "HfApi", lambda: _fake_hfapi(["model.safetensors", "config.json"]))
    assert huggingface.hub_format("mlx-community/Foo") is ModelFormat.mlx  # mlx-community publisher
    assert huggingface.hub_format("lmstudio-community/Foo-MLX-4bit") is ModelFormat.mlx  # "mlx" in name
    assert huggingface.hub_format("meta/Llama-Foo") is ModelFormat.safetensors  # plain safetensors

    monkeypatch.setattr(huggingface, "HfApi", lambda: _fake_hfapi(["pytorch_model.bin"]))
    assert huggingface.hub_format("pub/Weightless") is ModelFormat.unknown


def test_hf_pull_lands_in_format_bucket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(huggingface, "snapshot_download", lambda **k: str(k["local_dir"]))

    monkeypatch.setattr(huggingface, "hub_format", lambda *a, **k: ModelFormat.gguf)
    res = huggingface.pull("unsloth/Qwen3.5-4B-GGUF", tmp_path)
    assert res.destination.parts[-3:] == ("gguf", "unsloth", "Qwen3.5-4B-GGUF")  # format-centric

    monkeypatch.setattr(huggingface, "hub_format", lambda *a, **k: ModelFormat.mlx)
    res_mlx = huggingface.pull("mlx-community/Foo-4bit", tmp_path)
    assert res_mlx.destination.parts[-3:] == ("mlx", "mlx-community", "Foo-4bit")

    monkeypatch.setattr(huggingface, "hub_format", lambda *a, **k: ModelFormat.unknown)
    res_unknown = huggingface.pull("pub/Weightless", tmp_path)  # no weights → source bucket (old layout)
    assert res_unknown.destination.parts[-3:] == ("huggingface", "pub", "Weightless")


def test_scan_skips_incomplete_gguf_dir(tmp_path: Path) -> None:
    # A dir with only an mmproj (or nothing but partial downloads) is not yet a
    # runnable model; scanning it must skip it, not crash the whole scan.
    d = tmp_path / "gguf" / "pub" / "Repo"
    d.mkdir(parents=True)
    (d / "mmproj-Repo-BF16.gguf").write_bytes(b"x")
    assert library.scan(root=tmp_path) == []  # no runnable model, no exception

    # Once the real quant lands, it resolves normally (with the mmproj attached).
    (d / "Repo.Q4_K_M.gguf").write_bytes(b"weights")
    models = library.scan(root=tmp_path)
    assert len(models) == 1
    assert models[0].load_target.name == "Repo.Q4_K_M.gguf"
    assert models[0].mmproj is not None


def test_scan_detects_tts_model_with_vocoder(tmp_path: Path) -> None:
    # A dir with a model GGUF + a WavTokenizer vocoder is a TTS model: not a chat
    # model (generative=False), paired with the vocoder, with inferred languages.
    d = tmp_path / "tts" / "pub" / "OuteTTS-0.2-500M"
    d.mkdir(parents=True)
    (d / "OuteTTS-0.2-500M-Q4_K_M.gguf").write_bytes(b"model")
    (d / "WavTokenizer-Large-75.gguf").write_bytes(b"vocoder")

    models = library.scan(root=tmp_path)
    assert len(models) == 1
    m = models[0]
    assert m.tts is True
    assert m.generative is False  # excluded from the chat picker
    assert m.load_target.name.startswith("OuteTTS")
    assert m.vocoder is not None and m.vocoder.name.startswith("WavTokenizer")
    assert "en" in m.languages and "ja" in m.languages  # 0.2 → en/zh/ja/ko
    assert m.name == "pub/OuteTTS-0.2-500M"  # tts/ layout prefix stripped


def test_scan_skips_lone_vocoder(tmp_path: Path) -> None:
    # A dir with only a vocoder (no model) is not a runnable model.
    d = tmp_path / "tts" / "ggml-org" / "WavTokenizer"
    d.mkdir(parents=True)
    (d / "WavTokenizer-Large-75-F16.gguf").write_bytes(b"vocoder")
    assert library.scan(root=tmp_path) == []


def test_catalog_pull_include_rejects_non_hf(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only supported for the huggingface source"):
        catalog.pull(ModelSource.ollama, "model:tag", library_root=tmp_path, include=["*Q4*"])


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


def test_scan_keeps_cross_library_format_variants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Same repo, different formats across two libraries: a GGUF in one and an MLX
    # copy in the other must both survive dedup so --format can disambiguate them.
    drive, local = tmp_path / "drive", tmp_path / "local"
    (drive / "gguf" / "pub" / "Foo").mkdir(parents=True)
    (drive / "gguf" / "pub" / "Foo" / "m.gguf").write_bytes(b"g" * 100)
    (local / "mlx" / "pub" / "Foo").mkdir(parents=True)
    (local / "mlx" / "pub" / "Foo" / "weights.safetensors").write_bytes(b"m" * 100)

    monkeypatch.setattr(library, "roots", lambda *a, **k: [local, drive])
    formats = {m.model_format for m in library.find("Foo")}
    assert formats == {ModelFormat.gguf, ModelFormat.mlx}
    assert len(library.find("Foo", model_format=ModelFormat.mlx)) == 1


def test_scan_first_library_wins_on_tie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Same repo + same format in two libraries → one entry, from the FIRST (higher
    # priority) library; each model records the library it came from.
    drive, local = tmp_path / "drive", tmp_path / "local"
    (drive / "gguf" / "pub" / "Bar").mkdir(parents=True)
    (drive / "gguf" / "pub" / "Bar" / "m.gguf").write_bytes(b"g" * 100)
    (local / "gguf" / "pub" / "Bar").mkdir(parents=True)
    (local / "gguf" / "pub" / "Bar" / "m.gguf").write_bytes(b"g" * 100)

    monkeypatch.setattr(library, "roots", lambda *a, **k: [local, drive])  # local first = wins
    found = library.find("Bar")
    assert len(found) == 1
    assert found[0].load_target == local / "gguf" / "pub" / "Bar" / "m.gguf"
    assert found[0].library_root == local  # tags/metadata resolve against this library


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


def test_scan_spans_multiple_libraries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shared, local = tmp_path / "shared", tmp_path / "local"
    for root_dir, nm in ((shared, "Shared-GGUF"), (local, "Local-GGUF")):
        d = root_dir / "gguf" / "pub" / nm
        d.mkdir(parents=True)
        (d / "m.gguf").write_bytes(b"x" * 100)

    monkeypatch.setattr(library, "roots", lambda *a, **k: [local, shared])
    names = {m.name for m in library.scan()}  # no explicit root → spans all libraries
    assert names == {"pub/Shared-GGUF", "pub/Local-GGUF"}


def test_roots_resolves_project_libraries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A project's `libraries` list resolves relative paths against the cwd and the
    # `@shared` token to the machine default (library_root); no project → just the default.
    from kodo import project

    shared = tmp_path / "shared"
    monkeypatch.chdir(tmp_path)
    settings = Settings(library_root=shared)
    monkeypatch.setattr(library, "get_settings", lambda: settings)

    monkeypatch.setattr(project, "load", lambda *a, **k: None)  # no project
    assert library.roots(settings) == [shared.resolve()]

    proj = project.Project(libraries=[".kodo/library", "@shared"])
    monkeypatch.setattr(project, "load", lambda *a, **k: proj)
    assert library.roots(settings) == [(tmp_path / ".kodo/library").resolve(), shared.resolve()]


def test_roots_shared_token_strictness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # @shared resolves to the machine default (KODO_LIBRARY_ROOT). When that isn't set
    # explicitly, @shared must hard-fail if it's the only source — but drop out silently
    # if the project also ships its own local library (so a `--local` project is self-contained).
    from kodo import project

    monkeypatch.delenv("KODO_LIBRARY_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    # cwd is the empty tmp_path (no .env / kodo.toml) and the env var is unset, so
    # library_root is left at its default → "not set explicitly".
    settings = Settings()
    assert "library_root" not in settings.model_fields_set
    monkeypatch.setattr(library, "get_settings", lambda: settings)

    # Free-play (no project → implicit [@shared]) with no root set: refuse to run.
    monkeypatch.setattr(project, "load", lambda *a, **k: None)
    with pytest.raises(library.LibraryNotConfigured):
        library.roots(settings)

    # A project listing only @shared is equally unusable without a root.
    monkeypatch.setattr(project, "load", lambda *a, **k: project.Project(libraries=["@shared"]))
    with pytest.raises(library.LibraryNotConfigured):
        library.roots(settings)

    # But a project that also ships its own store runs from it; @shared just drops out.
    proj = project.Project(libraries=["library", "@shared"])
    monkeypatch.setattr(project, "load", lambda *a, **k: proj)
    assert library.roots(settings) == [(tmp_path / "library").resolve()]


def test_safe_join_allows_normal_names(tmp_path: Path) -> None:
    assert base.safe_join(tmp_path, "pub/repo") == (tmp_path / "pub/repo").resolve()
    assert base.safe_join(tmp_path, "gguf/pub/repo") == (tmp_path / "gguf/pub/repo").resolve()


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../secret", "pub/../../escape", "", "a/../../.."])
def test_safe_join_rejects_escapes(tmp_path: Path, bad: str) -> None:
    # An absolute path or a ``..`` that climbs out of the root must be refused.
    with pytest.raises(ValueError):
        base.safe_join(tmp_path, bad)


def test_lmstudio_pull_rejects_traversal_name(tmp_path: Path) -> None:
    # A malicious name must not let the source read/copy outside the LM Studio root.
    store = tmp_path / "lmstudio"
    (store / "pub" / "Real-GGUF").mkdir(parents=True)
    with pytest.raises(ValueError):
        lmstudio.pull("../../../../etc", tmp_path / "lib", models_dir=store)
